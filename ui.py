"""Shared UI helpers for both CLICKMINT bots.

Button-based menus (the user prefers buttons over typed commands) + a small
role-guard helper that routes owner / scoped-admin / user to different menus.

Callback-data prefixes used across the bots:
  menu:, reg:, terms:, cat:, style:, ntf:, post:, partner:, admin:, review:,
  contract:, chain:, chk:, report:, s:, sch:
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def row(btns: list[InlineKeyboardButton]) -> list[InlineKeyboardButton]:
    return btns


def back_btn(cb: str, label: str = "⬅️ Back") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=label, callback_data=cb)]


def _badge(value: int | None) -> str:
    if value is None:
        return ""
    value = max(0, int(value))
    return "99+" if value > 99 else str(value)


def main_menu(role: str, include_partnership: bool = True,
              available_count: int | None = None) -> InlineKeyboardMarkup:
    """The hub menu.

    ``include_partnership=False`` is used by the reward/exchange bot: reward
    exchange and partnership contracts are separate products and must not leak
    into one another's menus.
    """
    kb = []
    if role == "owner":
        kb += [[InlineKeyboardButton(text="🛠 Owner Panel (controls everyone)", callback_data="menu:owner")]]
    elif role == "admin":
        kb += [[InlineKeyboardButton(text="🛠 Admin Panel", callback_data="menu:admin")]]
    kb += [
        row([InlineKeyboardButton(text="📤 Submit a post", callback_data="menu:submit"),
             InlineKeyboardButton(text="📊 My post limit", callback_data="menu:cap")]),
        row([InlineKeyboardButton(text="📂 My channels / groups", callback_data="menu:channels")]),
        row([InlineKeyboardButton(text="📋 Task Marketplace", callback_data="menu:tasks")]),
        row([InlineKeyboardButton(text="📈 Credibility", callback_data="menu:credibility")]),
        row([InlineKeyboardButton(
            text=f"📬 Legacy posts available{(' · ' + _badge(available_count)) if available_count is not None else ''}",
            callback_data="menu:available")]),
        row([InlineKeyboardButton(text="🪙 MINT wallet", callback_data="menu:wallet")]),
    ]
    if include_partnership:
        kb.append(row([InlineKeyboardButton(text="🤝 Partnerships", callback_data="menu:partners"),
                       InlineKeyboardButton(text="📜 My contract", callback_data="menu:contract")]))
    if role in ("owner", "admin"):
        kb.append(row([InlineKeyboardButton(text="📁 Audit / Reports", callback_data="menu:audit")]))
    kb.append(row([InlineKeyboardButton(text="🏆 Leaderboard", callback_data="menu:rank")]))
    return InlineKeyboardMarkup(inline_keyboard=kb)


def audit_menu(counts: dict[str, int] | None = None) -> InlineKeyboardMarkup:
    counts = counts or {}
    def label(name: str, icon: str) -> str:
        return f"{icon} {name} · {_badge(counts.get(name, 0))}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label("Review Queue", "🧾"), callback_data="panel:review")],
        [InlineKeyboardButton(text=label("Pending Posts", "📬"), callback_data="panel:pending")],
        [InlineKeyboardButton(text=label("Scheduled Posts", "⏰"), callback_data="panel:scheduled")],
        [InlineKeyboardButton(text=label("Direct Delivery", "⚡"), callback_data="panel:direct")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")],
    ])


def role_menu(role: str, scope: str | None = None,
              counts: dict[str, int] | None = None) -> InlineKeyboardMarkup:
    """Owner/admin command panel (the 'different from user' menu)."""
    kb = [
        row([InlineKeyboardButton(text="📁 Audit log", callback_data="panel:audit"),
             InlineKeyboardButton(text="📤 Outbox", callback_data="panel:outbox")]),
        row([InlineKeyboardButton(text="📣 Broadcasts", callback_data="panel:broadcasts")]),
        row([InlineKeyboardButton(text="🚩 Pending reports", callback_data="panel:reports")]),
        row([InlineKeyboardButton(text="🧾 Review queue", callback_data="panel:review"),
             InlineKeyboardButton(text="📈 Performance", callback_data="panel:performance")]),
        row([InlineKeyboardButton(text="🏆 Leaderboard", callback_data="panel:rank")]),
        row([InlineKeyboardButton(text="⚡ Direct delivery", callback_data="panel:direct"),
             InlineKeyboardButton(text="⏰ Scheduled", callback_data="panel:scheduled")]),
    ]
    if role == "owner":
        kb.append(row([InlineKeyboardButton(text="👤 Manage admins", callback_data="panel:admins"),
                       InlineKeyboardButton(text="📜 Post terms", callback_data="panel:terms")]))
    kb.append(back_btn("menu:hub", "⬅️ Back to main"))
    return InlineKeyboardMarkup(inline_keyboard=kb)
