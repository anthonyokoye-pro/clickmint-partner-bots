"""Read-only Telegram staging smoke test.

Run only with explicit staging environment variables:

  STAGING_BOT_TOKEN=... STAGING_CHAT_ID=@channel \
    /tmp/cmvenv/bin/python staging_telegram_check.py

The check never sends, edits, deletes, pins, or forwards a message. It verifies
only the official Bot API operations needed by ClickMint destination onboarding.
Credentials are read from the environment and never included in output.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys


async def run() -> dict:
    token = os.environ.get("STAGING_BOT_TOKEN", "").strip()
    chat_ref = os.environ.get("STAGING_CHAT_ID", "").strip()
    if not token or not chat_ref:
        return {"ok": False, "skipped": True,
                "error": "set STAGING_BOT_TOKEN and STAGING_CHAT_ID to run the read-only staging check"}
    from aiogram import Bot

    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        chat = await bot.get_chat(chat_ref)
        member = await bot.get_chat_member(chat.id, me.id)
        count = None
        count_error = None
        try:
            count = await bot.get_chat_member_count(chat.id)
        except Exception as exc:  # counts can be unavailable by chat type/rights
            count_error = type(exc).__name__
        member_data = member.model_dump() if hasattr(member, "model_dump") else dict(member)
        chat_data = chat.model_dump() if hasattr(chat, "model_dump") else dict(chat)
        permissions = {key: bool(member_data.get(key)) for key in (
            "can_post_messages", "can_edit_messages", "can_delete_messages", "can_invite_users")
            if key in member_data}
        reasons = []
        if member_data.get("status") not in {"administrator", "creator"}:
            reasons.append("staging bot is not an administrator")
        if chat_data.get("type") == "channel" and not permissions.get("can_post_messages", False):
            reasons.append("staging bot lacks can_post_messages")
        return {"ok": not reasons, "skipped": False,
                "bot": {"id": me.id, "username": me.username},
                "chat": {"id": chat_data.get("id"), "type": chat_data.get("type"),
                         "title": chat_data.get("title"), "username": chat_data.get("username")},
                "member_status": member_data.get("status"), "permissions": permissions,
                "member_count": count, "member_count_error": count_error, "reasons": reasons}
    finally:
        await bot.session.close()


def main() -> int:
    report = asyncio.run(run())
    print(json.dumps(report, indent=2, sort_keys=True))
    # Missing staging configuration is intentionally a non-failing skip for CI;
    # once configured, Telegram failures fail the command.
    return 0 if report.get("ok") or report.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
