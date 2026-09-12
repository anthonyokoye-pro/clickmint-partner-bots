"""
ADMIN PANEL BOT  (aiogram)  — the owner's live dashboard.

THIRD bot, but it is NOT extra hosting: it reads the SAME JSON store files the reward
and partnership bots write, and runs as one more process on the same free machine.

What it shows / does (all by BUTTON, no typed commands):
  • Live dashboard  — members, review queue, open contracts
  • Daily caps      — every channel's capped slots (size x performance)
  • Review queue    — both networks; approve / reject a queued post by button
                      (the matching network bot DMs the sender the outcome)
  • Contracts       — open / close-requested / closed partner contracts
  • Admin logins    — generate ONE-TIME invite codes by button (reward / partnership /
                      both). An admin redeems it in the network bot with /adminlogin.

IMPORTANT: the owner's numeric Telegram id is OWNER_USER_ID. The dashboard is read
from reward_ledger.json + partnership_state.json (the same files the other bots use).
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from store import JsonStore
from verification_store import VerificationStore
from core import CreditLedger, PerformanceEngine, ReportRegistry
from mint_ledger import TransactionalMintLedger
from user_directory import UserDirectory
from governance import ReviewQueue, RoleRegistry, daily_post_cap
from platform_store import SharedKV
from broadcast_queue import BroadcastQueue
from tg_entities import sanitize_entities, to_html
from features import category_counts
from channel_registry import ChannelRegistry
from enforcement import EnforcementStore, EnforcementError
import config

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config.ADMIN_BOT_TOKEN
OWNER_USER_ID = config.OWNER_USER_ID

# The SAME files the network bots use. Paths must match what reward/partnership write.
REWARD_STORE_PATH = config.REWARD_STORE_PATH
PARTNER_STORE_PATH = config.PARTNER_STORE_PATH


def load_stores():
    rstore, pstore = JsonStore(REWARD_STORE_PATH), JsonStore(PARTNER_STORE_PATH)
    # Destinations/credentials moved to the shared SQLite verification DB; wrap
    # the reward store so its managed_channels reads hit the live table.
    return VerificationStore(config.VERIFICATION_DB_PATH, legacy=rstore), pstore


def _roles_users() -> dict:
    """Roles live in the shared platform DB (one table for every bot)."""
    return SharedKV(config.PLATFORM_DB_PATH).get("roles_users", {}) or {}


enforcement = EnforcementStore(config.ENFORCEMENT_DB_PATH)


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back(cb, label="⬅️ Back"):
    return InlineKeyboardButton(text=label, callback_data=cb)


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def is_owner(uid) -> bool:
    """Owner by numeric id. An unset OWNER_USER_ID (0) must match NOBODY, or an
    unconfigured deployment would hand the panel to the first user with id 0."""
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return False
    return bool(OWNER_USER_ID) and uid == OWNER_USER_ID


# ---------------------------------------------------------------------------
# /start -> dashboard (owner only)
# ---------------------------------------------------------------------------
# `@dp.message(types.Message)` (the old filter) passed the Message *class* as a
# filter. aiogram calls a filter with the event, so every incoming message tried
# to build a Message out of a Message and the dashboard never opened.
@dp.message(Command("start"))
async def start(msg: types.Message):
    if not is_owner(msg.from_user.id):
        await msg.answer("🔒 This is the owner's admin panel.")
        return
    await show_dashboard(msg)


@dp.message()
async def any_message(msg: types.Message):
    """Owner-only panel. A plain text message from the owner becomes a Reward
    broadcast DRAFT with its Telegram formatting captured verbatim
    (text + entities, decision 2026-09-09 #3). Anything else re-opens the dashboard."""
    if not is_owner(msg.from_user.id):
        await msg.answer("🔒 This is the owner's admin panel.")
        return
    text = msg.text or ""
    if text and not text.startswith("/"):
        await _capture_broadcast_draft(msg, text)
        return
    await show_dashboard(msg)


async def _capture_broadcast_draft(msg: types.Message, text: str):
    """Store exactly what Telegram parsed: no markup is typed or interpreted here."""
    try:
        entities = sanitize_entities(text, msg.entities or [])
    except ValueError as exc:
        await msg.answer(f"Could not capture formatting: {exc}. Draft not saved.")
        return
    title = text.strip().splitlines()[0][:80]
    campaign_id = BroadcastQueue(config.BROADCAST_DB_PATH).create_campaign(
        bot_scope="reward", created_by=msg.from_user.id, title=title,
        payload={"text": text, "entities": entities, "audience": "known_reward_members",
                 "composed_in": "telegram"})
    kinds = sorted({e["type"] for e in entities})
    await msg.answer(
        "📝 Saved as Reward broadcast DRAFT.\n"
        f"  • id: {campaign_id}\n"
        f"  • formatting captured: {', '.join(kinds) if kinds else 'none (plain text)'}\n\n"
        "Nothing is sent yet. Preview and queue it in the Admin Mini App, or delete it below.",
        reply_markup=_kb([[InlineKeyboardButton(text="🗑 Delete draft", callback_data=f"bcdel:{campaign_id}")],
                          [_back("dash:back")]]))


@dp.callback_query(lambda c: c.data and c.data.startswith("bcdel:"))
async def delete_draft(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    ok = BroadcastQueue(config.BROADCAST_DB_PATH).delete_draft(cb.data.split(":", 1)[1])
    await cb.answer("Draft deleted." if ok else "Only a draft can be deleted (it may already be queued).", show_alert=not ok)
    if ok:
        await _safe_edit(cb, "🗑 Draft deleted.", _kb([[_back("dash:back")]]))


def _dashboard_text():
    rstore, pstore = load_stores()
    rledger = CreditLedger(rstore)
    rrev = ReviewQueue(rstore)
    prev = ReviewQueue(pstore)
    ledger_members = len(UserDirectory(TransactionalMintLedger(config.MINT_DB_PATH)).list_active_user_ids())
    pending_reward = len(rrev.pending())
    pending_partner = len(prev.pending())
    # open contracts (ACTIVE / RENEWED / CLOSE_REQUESTED) in partnership store
    contracts = pstore.get("partner_contracts", [])
    open_c = [c for c in contracts if c.get("status") in ("ACTIVE", "RENEWED", "CLOSE_REQUESTED")]
    # pending reports live in the reward store and need a HUMAN decision
    pending_reports = len(ReportRegistry(rstore).pending()) + len(ReportRegistry(pstore).pending())
    # Category counts are privileged dashboard data; ordinary users never see it.
    category_rows = list((rstore.get("managed_channels", {}) or {}).values())
    category_summary = category_counts(category_rows)
    category_text = ", ".join(f"{k}: {v}" for k, v in sorted(category_summary.items())) or "none"
    # admin logins across both stores
    admins = {uid: u for uid, u in _roles_users().items()
              if u.get("role") == "admin" and u.get("active") is not False}
    lines = [
        "🛠 CLICKMINT ADMIN DASHBOARD",
        f"  • Registered channels/groups: {ledger_members}",
        f"  • Categories (privileged): {category_text}",
        f"  • Review queue: reward {pending_reward} · partnership {pending_partner}",
        f"  • Open partnership contracts: {len(open_c)}",
        f"  • Pending reports awaiting a human: {pending_reports}",
        f"  • Active admins: {len(admins)}",
        "",
        "Tap below to drill into any panel.",
    ]
    return "\n".join(lines)


def _dashboard_kb():
    return _kb([
        [InlineKeyboardButton(text="📊 Daily caps (size x performance)", callback_data="dash:cap")],
        [InlineKeyboardButton(text="🪙 Mint ledger / referrals", callback_data="dash:mint")],
        [InlineKeyboardButton(text="🧾 Review queue — reward", callback_data="dash:rev:reward"),
         InlineKeyboardButton(text="🧾 Review queue — partner", callback_data="dash:rev:partnership")],
        [InlineKeyboardButton(text="🤝 Partnership contracts", callback_data="dash:contracts")],
        [InlineKeyboardButton(text="👤 Admin logins", callback_data="dash:admins")],
        [InlineKeyboardButton(text="🛡 Trust & safety", callback_data="dash:safety")],
    ])


async def show_dashboard(msg):
    await msg.answer(_dashboard_text(), reply_markup=_dashboard_kb())


@dp.callback_query(lambda c: c.data and c.data.startswith("dash:"))
async def dash(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    parts = cb.data.split(":")
    which = parts[1]
    if which == "back":
        # `dash:back` also starts with "dash:", so this handler matched it first
        # and the dedicated back handler below never ran — the Back button was
        # a no-op on every panel.
        await _safe_edit(cb, _dashboard_text(), _dashboard_kb())
    elif which == "cap":
        await show_caps(cb)
    elif which == "mint":
        await show_mint(cb)
    elif which == "rev":
        await show_review(cb, parts[2] if len(parts) > 2 else "reward")
    elif which == "contracts":
        await show_contracts(cb)
    elif which == "admins":
        await show_admins(cb)
    elif which == "safety":
        await show_safety(cb)
    await cb.answer()


# --- caps ---
async def show_caps(cb):
    rstore, _ = load_stores()
    rledger = CreditLedger(rstore)
    rperf = PerformanceEngine(rledger, rstore, None)
    lines = ["📊 DAILY POST CAPS  (size x performance)", ""]
    for username, m in rledger.ledger.items():
        if m.get("is_owner"):
            continue
        s = rperf.score(username)
        row = (rstore.get("managed_channels", {}) or {}).get(username)
        connected = bool(row and row.get("verified_state") == "VERIFIED"
                         and row.get("bot_added") is True
                         and row.get("status") == "ACTIVE")
        cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"),
                             connected=connected)
        cap_txt = "unlimited" if cap == -1 else str(cap)
        verification = row.get("verified_state", "REGISTERED") if row else "REGISTERED"
        reason = ("; ".join(row.get("verification_reasons") or []) if row else "")
        detail = f" — {reason[:100]}" if reason else ""
        lines.append(f"{username:<22} performance tier {s['band']}  {verification}  cap={cap_txt}  ({m.get('size',0)} members){detail}")
    await _safe_edit(cb, "\n".join(lines[:50]), _kb([[_back("dash:back")]]))


# --- transactional Mint and referral review ---
async def show_mint(cb):
    tx = TransactionalMintLedger(config.MINT_DB_PATH)
    summary = tx.ledger_summary(limit=12)
    lines = ["🪙 MINT LEDGER / REFERRALS", "", f"Accounts: {summary['accounts']}"]
    for row in summary["entries"][:12]:
        lines.append(f"• {row['entry_type']} · {row['direction']} · {row['state']} · "
                     f"{row['amount']} ({row['n']} entry/entries)")
    recent = summary["recent"]
    rows = []
    if recent:
        lines += ["", "Recent entries:"]
        for entry in recent[:8]:
            lines.append(f"{entry['entry_id']} · user {entry['user_id']} · "
                         f"{entry['direction']} {entry['amount']} · {entry['entry_type']} · {entry['state']}")
            if entry["state"] == "confirmed":
                rows.append([InlineKeyboardButton(
                    text=f"↩️ Reverse {entry['entry_id'][-6:]}",
                    callback_data=f"mint:reverse:{entry['entry_id']}")])
    rows.append([_back("dash:back")])
    await _safe_edit(cb, "\n".join(lines[:45]), _kb(rows))


@dp.callback_query(lambda c: c.data and c.data.startswith("mint:"))
async def mint_action(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    parts = cb.data.split(":", 2)
    if len(parts) != 3 or parts[1] != "reverse":
        await cb.answer("Unknown Mint action.", show_alert=True)
        return
    entry_id = parts[2]
    tx = TransactionalMintLedger(config.MINT_DB_PATH)
    try:
        reverse_id = tx.reverse(entry_id, idempotency_key=f"admin-reversal:{entry_id}",
                                reason=f"owner {cb.from_user.id} reversed from admin panel")
        tx.add_audit_event(actor_type="telegram_owner", actor_id=str(cb.from_user.id),
                           action="reverse_mint_entry", object_type="mint_entry",
                           object_id=entry_id, reason="owner admin panel action")
    except Exception as exc:
        # Protect the admin panel from a stale button or a balance/race error;
        # the detailed reason stays in server logs.
        logging.warning("Mint reversal failed for %s: %s", entry_id, exc)
        await cb.answer("That Mint entry could not be reversed.", show_alert=True)
        return
    await cb.answer(f"Reversed: {reverse_id}")
    await show_mint(cb)


# --- review queue ---
async def show_review(cb, scope):
    st = JsonStore(REWARD_STORE_PATH if scope == "reward" else PARTNER_STORE_PATH)
    rq = ReviewQueue(st)
    pending = rq.pending()
    if not pending:
        await _safe_edit(cb, "✅ No posts awaiting review.",
                         _kb([[_back("dash:back")]]))
        return
    lines = [f"🧾 {scope} review queue — {len(pending)} item(s):", ""]
    rows = []
    for it in pending[:8]:
        lines.append(f"#{it['id']} | {it.get('category')} | by {it.get('sender')}\n  {it.get('text','')[:70]}")
        rows.append([InlineKeyboardButton(text=f"✅ Approve #{it['id']}", callback_data=f"rv:approve:{scope}:{it['id']}"),
                     InlineKeyboardButton(text=f"❌ Reject #{it['id']}", callback_data=f"rv:reject:{scope}:{it['id']}")])
    rows.append([_back("dash:back")])
    await _safe_edit(cb, "\n".join(lines[:40]), _kb(rows))


@dp.callback_query(lambda c: c.data and c.data.startswith("rv:"))
async def review_decision(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    _, action, scope, rid = cb.data.split(":")
    if scope not in ("reward", "partnership"):
        await cb.answer("Unknown queue.", show_alert=True)
        return
    st = JsonStore(REWARD_STORE_PATH if scope == "reward" else PARTNER_STORE_PATH)
    rq = ReviewQueue(st)
    if not rq.decide(int(rid), approve=(action == "approve"),
                     note=f"{action} by owner-admin"):
        await cb.answer("That queue item no longer exists.", show_alert=True)
        await show_review(cb, scope)
        return
    # Hand the outcome to the network bot (which owns the sender's chat) to DM sender.
    rq.mark_notify(int(rid), via=scope)
    await cb.answer(f"#{rid} {action}d. Sender will be notified by the network bot.")
    await show_review(cb, scope)


# --- contracts ---
async def show_contracts(cb):
    _, pstore = load_stores()
    contracts = pstore.get("partner_contracts", [])
    if not contracts:
        await _safe_edit(cb, "No partnership contracts yet.",
                         _kb([[_back("dash:back")]]))
        return
    lines = ["🤝 PARTNERSHIP CONTRACTS", ""]
    for c in contracts[-15:]:
        lines.append(f"[{c.get('status')}] {c.get('a')} <-> {c.get('b')}  (opened {c.get('opened')})\n"
                     f"    {c.get('contract', {})}")
    await _safe_edit(cb, "\n".join(lines[:40]), _kb([[_back("dash:back")]]))


# --- admin logins (generate invite codes by button) ---
async def show_admins(cb):
    rstore, pstore = load_stores()
    all_admins = {uid: u for uid, u in _roles_users().items() if u.get("role") == "admin"}
    lines = ["👤 ADMIN LOGINS", "",
             "How it works: an admin must CONTACT the owner first. Then tap Generate a "
             "one-time code below and send it to them. They redeem it in the network bot "
             "with /adminlogin <CODE>. Admins are SCOPED (reward / partnership / both).", ""]
    if all_admins:
        lines.append("Current admins:")
        for uid, u in all_admins.items():
            lines.append(f"  • id {uid}  scope {u.get('scope')}  active={u.get('active')}")
    rows = [
        [InlineKeyboardButton(text="🔑 Invite code — reward", callback_data="inv:reward")],
        [InlineKeyboardButton(text="🔑 Invite code — partnership", callback_data="inv:partnership")],
        [InlineKeyboardButton(text="🔑 Invite code — both", callback_data="inv:both")],
        [InlineKeyboardButton(text="🔑 Invite code — Ads Manager", callback_data="inv:ads")],
        [_back("dash:back")],
    ]
    await _safe_edit(cb, "\n".join(lines), _kb(rows))


@dp.callback_query(lambda c: c.data and c.data.startswith("inv:"))
async def gen_invite(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    scope = cb.data.split(":")[1]
    scopes = {"reward": ["reward"], "partnership": ["partnership"],
              "both": ["reward", "partnership"], "ads": ["ads"]}.get(scope)
    if scopes is None:
        await cb.answer("Unknown scope.", show_alert=True)
        return
    # Roles are shared across bots (platform DB), so ONE code carries the whole
    # scope list and /adminlogin works in whichever bot the admin uses.
    gen = RoleRegistry(SharedKV(config.PLATFORM_DB_PATH), owner_user_id=OWNER_USER_ID)
    label = " + ".join(scopes)
    if "ads" in scopes:
        # Ads Manager grants NO network moderation power — only ad drafting.
        label = "ads manager (drafting only)"
    created = [(label, gen.create_invite(scopes, created_by=int(OWNER_USER_ID)))]
    lines = ["🔑 One-time admin invite code(s) generated:", ""]
    for sc, code in created:
        lines.append(f"• {sc}:  {code}")
    lines += ["", "Send the code(s) to the trusted person. They run:",
              "   /adminlogin <CODE>", "in that bot. Each code is single-use."]
    await _safe_edit(cb, "\n".join(lines), _kb([[_back("dash:admins")]]))


# --- trust & safety (compliance store) ---
def _safety_text():
    st = enforcement.safety_status()
    safe = st["safe_mode"]
    states = st["entities_by_state"] or {}
    lines = ["🛡 TRUST & SAFETY",
             f"  • Emergency safe mode: {'ON ⛔' if safe.get('enabled') else 'off'}" +
             (f" — {safe.get('reason')}" if safe.get('enabled') else ""),
             f"  • Open reports: {st['open_reports']}   • Open appeals: {st['open_appeals']}",
             "  • Entities: " + (", ".join(f"{k} {v}" for k, v in sorted(states.items())) or "none"),
             ""]
    reports = enforcement.list_reports("PENDING", limit=5)
    if reports:
        lines.append("Latest pending reports:")
        lines += [f"  #{r['report_id'][-6:]} {r['entity_type']} {r['entity_id']} — {r['reason'][:60]}" for r in reports]
    appeals = enforcement.list_appeals("OPEN", limit=5)
    if appeals:
        lines.append("Open appeals:")
        lines += [f"  {a['appeal_id'][-6:]} {a['entity_type']} {a['entity_id']} — {a['statement'][:60]}" for a in appeals]
    lines += ["", "Reports and appeals are decided by a human in the Admin Mini App.",
              "Nothing here is automatic; this bot only toggles emergency safe mode."]
    return "\n".join(lines)


def _safety_kb():
    on = enforcement.emergency_enabled("safe_mode")
    return _kb([
        [InlineKeyboardButton(text=("🟢 Disable safe mode" if on else "🔴 Enable safe mode"),
                              callback_data="safety:" + ("off" if on else "on"))],
        [_back("dash:back")],
    ])


async def show_safety(cb):
    await _safe_edit(cb, _safety_text(), _safety_kb())


@dp.callback_query(lambda c: c.data and c.data.startswith("safety:"))
async def safety_toggle(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    enable = cb.data == "safety:on"
    try:
        enforcement.set_emergency(enable, actor_id=cb.from_user.id,
                                  reason="toggled from admin bot by owner")
    except EnforcementError as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    await show_safety(cb)
    await cb.answer("Safe mode " + ("enabled — member automation is paused." if enable else "disabled."))


async def _safe_edit(cb, text, kb):
    """Edit a panel, tolerating Telegram's "message is not modified" error —
    re-rendering an unchanged screen must not raise at the user."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logging.warning("panel edit failed: %s", e)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
