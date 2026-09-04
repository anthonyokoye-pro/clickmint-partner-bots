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
    return [InlineKeyboardButton(label, callback_data=cb)]


def main_menu(role: str) -> InlineKeyboardMarkup:
    """The hub menu. Owner/Admin get extra panels; users get the normal menu."""
    kb = []
    if role == "owner":
        kb += [[InlineKeyboardButton("🛠 Owner Panel (controls everyone)", callback_data="menu:owner")]]
    elif role == "admin":
        kb += [[InlineKeyboardButton("🛠 Admin Panel", callback_data="menu:admin")]]
    kb += [
        row([InlineKeyboardButton("📤 Submit a post", callback_data="menu:submit"),
             InlineKeyboardButton("📊 My post limit", callback_data="menu:cap")]),
        row([InlineKeyboardButton("🤝 Partnerships", callback_data="menu:partners"),
             InlineKeyboardButton("📜 My contract", callback_data="menu:contract")]),
        row([InlineKeyboardButton("📁 Audit / Reports", callback_data="menu:audit"),
             InlineKeyboardButton("🏆 Rank / leaderboard", callback_data="menu:rank")]),
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def role_menu(role: str, scope: str | None = None) -> InlineKeyboardMarkup:
    """Owner/admin command panel (the 'different from user' menu)."""
    kb = [
        row([InlineKeyboardButton("📁 Audit log", callback_data="panel:audit"),
             InlineKeyboardButton("🚩 Pending reports", callback_data="panel:reports")]),
        row([InlineKeyboardButton("🧾 Review queue", callback_data="panel:review"),
             InlineKeyboardButton("🏆 Rank", callback_data="panel:rank")]),
        row([InlineKeyboardButton("⚡ Direct delivery", callback_data="panel:direct"),
             InlineKeyboardButton("⏰ Scheduled", callback_data="panel:scheduled")]),
    ]
    if role == "owner":
        kb.append(row([InlineKeyboardButton("👤 Manage admins", callback_data="panel:admins"),
                       InlineKeyboardButton("📜 Post terms", callback_data="panel:terms")]))
    kb.append(back_btn("menu:hub", "⬅️ Back to main"))
    return InlineKeyboardMarkup(inline_keyboard=kb)
