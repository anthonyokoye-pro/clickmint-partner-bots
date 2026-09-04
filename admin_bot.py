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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from store import JsonStore
from core import CreditLedger, PerformanceEngine
from governance import ReviewQueue, RoleRegistry, daily_post_cap
import config

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config.ADMIN_BOT_TOKEN
OWNER_USER_ID = config.OWNER_USER_ID

# The SAME files the network bots use. Paths must match what reward/partnership write.
REWARD_STORE_PATH = config.REWARD_STORE_PATH
PARTNER_STORE_PATH = config.PARTNER_STORE_PATH


def load_stores():
    return JsonStore(REWARD_STORE_PATH), JsonStore(PARTNER_STORE_PATH)


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back(cb, label="⬅️ Back"):
    return InlineKeyboardButton(label, callback_data=cb)


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def is_owner(uid) -> bool:
    return int(uid) == OWNER_USER_ID


# ---------------------------------------------------------------------------
# /start -> dashboard (owner only)
# ---------------------------------------------------------------------------
@dp.message(types.Message)
async def start(msg: types.Message):
    if not is_owner(msg.from_user.id):
        await msg.answer("🔒 This is the owner's admin panel.")
        return
    await show_dashboard(msg)


def _dashboard_text():
    rstore, pstore = load_stores()
    rledger = CreditLedger(rstore)
    rperf = PerformanceEngine(rledger, rstore, None)
    rrev = ReviewQueue(rstore)
    prev = ReviewQueue(pstore)
    ledger_members = len(rledger.ledger)
    pending_reward = len(rrev.pending())
    pending_partner = len(prev.pending())
    # open contracts (ACTIVE / RENEWED / CLOSE_REQUESTED) in partnership store
    contracts = pstore.get("partner_contracts", [])
    open_c = [c for c in contracts if c.get("status") in ("ACTIVE", "RENEWED", "CLOSE_REQUESTED")]
    # admin logins across both stores
    admins = {}
    for st in (rstore, pstore):
        for uid, u in (st.get("roles_users", {}) or {}).items():
            if u.get("role") == "admin":
                admins[uid] = u
    lines = [
        "🛠 CLICKMINT ADMIN DASHBOARD",
        f"  • Registered channels: {ledger_members}",
        f"  • Review queue: reward {pending_reward} · partnership {pending_partner}",
        f"  • Open partnership contracts: {len(open_c)}",
        f"  • Active admins: {len(admins)}",
        "",
        "Tap below to drill into any panel.",
    ]
    return "\n".join(lines)


def _dashboard_kb():
    return _kb([
        [InlineKeyboardButton("📊 Daily caps (size x performance)", callback_data="dash:cap")],
        [InlineKeyboardButton("🧾 Review queue — reward", callback_data="dash:rev:reward"),
         InlineKeyboardButton("🧾 Review queue — partner", callback_data="dash:rev:partnership")],
        [InlineKeyboardButton("🤝 Partnership contracts", callback_data="dash:contracts")],
        [InlineKeyboardButton("👤 Admin logins", callback_data="dash:admins")],
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
    if which == "cap":
        await show_caps(cb)
    elif which == "rev":
        await show_review(cb, parts[2])
    elif which == "contracts":
        await show_contracts(cb)
    elif which == "admins":
        await show_admins(cb)
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
        cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"))
        cap_txt = "unlimited" if cap == -1 else str(cap)
        lines.append(f"{username:<22} {s['band']}  {s['status']}  cap={cap_txt}  ({m.get('size',0)} sub)")
    await cb.message.edit_text("\n".join(lines[:50]),
                               reply_markup=_kb([[_back("dash:back")]]))


# --- review queue ---
async def show_review(cb, scope):
    st = JsonStore(REWARD_STORE_PATH if scope == "reward" else PARTNER_STORE_PATH)
    rq = ReviewQueue(st)
    pending = rq.pending()
    if not pending:
        await cb.message.edit_text("✅ No posts awaiting review.",
                                   reply_markup=_kb([[_back("dash:back")]]))
        return
    lines = [f"🧾 {scope} review queue — {len(pending)} item(s):", ""]
    rows = []
    for it in pending[:8]:
        lines.append(f"#{it['id']} | {it.get('category')} | by {it.get('sender')}\n  {it.get('text','')[:70]}")
        rows.append([InlineKeyboardButton(f"✅ Approve #{it['id']}", callback_data=f"rv:approve:{scope}:{it['id']}"),
                     InlineKeyboardButton(f"❌ Reject #{it['id']}", callback_data=f"rv:reject:{scope}:{it['id']}")])
    rows.append([_back("dash:back")])
    await cb.message.edit_text("\n".join(lines[:40]), reply_markup=_kb(rows))


@dp.callback_query(lambda c: c.data and c.data.startswith("rv:"))
async def review_decision(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    _, action, scope, rid = cb.data.split(":")
    st = JsonStore(REWARD_STORE_PATH if scope == "reward" else PARTNER_STORE_PATH)
    rq = ReviewQueue(st)
    rq.decide(int(rid), approve=(action == "approve"), note=f"{action} by owner-admin")
    # Hand the outcome to the network bot (which owns the sender's chat) to DM sender.
    rq.mark_notify(int(rid), via=scope)
    await cb.answer(f"#{rid} {action}d. Sender will be notified by the network bot.")
    await show_review(cb, scope if scope else "reward")


# --- contracts ---
async def show_contracts(cb):
    _, pstore = load_stores()
    contracts = pstore.get("partner_contracts", [])
    if not contracts:
        await cb.message.edit_text("No partnership contracts yet.",
                                   reply_markup=_kb([[_back("dash:back")]]))
        return
    lines = ["🤝 PARTNERSHIP CONTRACTS", ""]
    for c in contracts[-15:]:
        lines.append(f"[{c.get('status')}] {c.get('a')} <-> {c.get('b')}  (opened {c.get('opened')})\n"
                     f"    {c.get('contract', {})}")
    await cb.message.edit_text("\n".join(lines[:40]), reply_markup=_kb([[_back("dash:back")]]))


# --- admin logins (generate invite codes by button) ---
async def show_admins(cb):
    rstore, pstore = load_stores()
    all_admins = {}
    for st in (rstore, pstore):
        for uid, u in (st.get("roles_users", {}) or {}).items():
            if u.get("role") == "admin":
                all_admins[uid] = u
    lines = ["👤 ADMIN LOGINS", "",
             "How it works: an admin must CONTACT the owner first. Then tap Generate a "
             "one-time code below and send it to them. They redeem it in the network bot "
             "with /adminlogin <CODE>. Admins are SCOPED (reward / partnership / both).", ""]
    if all_admins:
        lines.append("Current admins:")
        for uid, u in all_admins.items():
            lines.append(f"  • id {uid}  scope {u.get('scope')}  active={u.get('active')}")
    rows = [
        [InlineKeyboardButton("🔑 Invite code — reward", callback_data="inv:reward")],
        [InlineKeyboardButton("🔑 Invite code — partnership", callback_data="inv:partnership")],
        [InlineKeyboardButton("🔑 Invite code — both", callback_data="inv:both")],
        [_back("dash:back")],
    ]
    await cb.message.edit_text("\n".join(lines), reply_markup=_kb(rows))


@dp.callback_query(lambda c: c.data and c.data.startswith("inv:"))
async def gen_invite(cb: types.CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    scope = cb.data.split(":")[1]
    scopes = {"reward": ["reward"], "partnership": ["partnership"],
              "both": ["reward", "partnership"]}[scope]
    # A 'reward' code goes into the reward store's invite list; 'partnership' into the
    # partner store; 'both' into both — so /adminlogin works in whichever bot they use.
    created = []
    rstore, pstore = load_stores()
    gen_r = RoleRegistry(rstore, owner_user_id=OWNER_USER_ID)
    gen_p = RoleRegistry(pstore, owner_user_id=OWNER_USER_ID)
    if "reward" in scopes:
        created.append(("reward", gen_r.create_invite(["reward"], created_by=int(OWNER_USER_ID))))
    if "partnership" in scopes:
        created.append(("partnership", gen_p.create_invite(["partnership"], created_by=int(OWNER_USER_ID))))
    lines = ["🔑 One-time admin invite code(s) generated:", ""]
    for sc, code in created:
        lines.append(f"• {sc}:  {code}")
    lines += ["", "Send the code(s) to the trusted person. They run:",
              "   /adminlogin <CODE>", "in that bot. Each code is single-use."]
    await cb.message.edit_text("\n".join(lines), reply_markup=_kb([[_back("dash:admins")]]))


# --- back button ---
@dp.callback_query(lambda c: c.data == "dash:back")
async def dash_back(cb: types.CallbackQuery):
    await cb.message.edit_text(_dashboard_text(), reply_markup=_dashboard_kb())
    await cb.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
