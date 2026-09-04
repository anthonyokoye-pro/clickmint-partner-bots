"""CLICKMINT branding — the single source of truth in code.

The public identity of the three bots is specified in `docs/BOT_BRANDING.md`.
Retyping those strings into @BotFather by hand is how they drift apart, so the
exact approved copy lives here and can be pushed to Telegram with the Bot API:

    python3 branding.py            # applies name/description/about/commands
    python3 branding.py --check    # prints what WOULD be applied, changes nothing

Only the **username** cannot be set this way — it is permanent and must be
chosen in @BotFather (candidates are listed in the branding doc).

Every string below is length-checked against Telegram's documented limits:
  name ≤ 64 · description ≤ 512 · short description (About) ≤ 120
"""
from __future__ import annotations

import asyncio
import sys

NAME_LIMIT = 64
DESCRIPTION_LIMIT = 512
SHORT_DESCRIPTION_LIMIT = 120

REWARD = {
    "key": "reward",
    "token_env": "REWARD_BOT_TOKEN",
    "name": "CLICKMINT — Partner & Reward Network",
    "description": (
        "CLICKMINT is a partner & reward network for AI, crypto and airdrop "
        "channels.\n\n"
        "Earn credits by sharing another channel's post, then spend them to get your "
        "own posts shared across the network. Every post is vetted: niche content "
        "only, no spam, no money-asking, no scam, no third-party ads.\n\n"
        "Performance beats size — your daily cap scales with size x how well you "
        "actually perform, so small high-engagement channels get a fair shot.\n\n"
        "Partner with channels you choose, agree a contract, and the owner mediates."
    ),
    "short_description": (
        "Verified partner & reward network for AI/crypto channels. Earn credits, share "
        "posts, partner safely."
    ),
    "commands": [
        ("start", "Open the menu / register your channel"),
        ("register", "Register: /register @yourchannel <subscribers>"),
        ("balance", "Your credits, earned and spent"),
        ("adminlogin", "Redeem a one-time admin invite code"),
    ],
}

PARTNERSHIP = {
    "key": "partnership",
    "token_env": "PARTNER_BOT_TOKEN",
    "name": "CLICKMINT Partnerships",
    "description": (
        "Curated partnership exchange for quality AI/crypto/airdrop channels.\n\n"
        "Choose a partner, agree a contract — post types, volume, schedule, duration — and "
        "both sides honour it. Niche-related content only, no spam, no ads, no scams. The "
        "owner is the mediator: you're notified on open, renew and close, and a partnership "
        "can't just vanish.\n\n"
        "Ideal for channels that want to cross-promote with a trusted, complementary partner "
        "without losing quality or trust."
    ),
    "short_description": (
        "Cross-promote with trusted complementary channels. Contract-bound, owner-mediated, "
        "safe."
    ),
    "commands": [
        ("start", "Open the menu / register as a partner"),
        ("contract", "Set the post types you accept and receive"),
        ("audit", "Delivery audit log (owner/admin)"),
        ("adminlogin", "Redeem a one-time admin invite code"),
    ],
}

ADMIN = {
    "key": "admin",
    "token_env": "ADMIN_BOT_TOKEN",
    "name": "CLICKMINT Admin",
    "description": (
        "Owner dashboard for the CLICKMINT partner network. View live daily caps, approve or "
        "reject queued posts, review open contracts, and generate one-time admin invite "
        "codes — all by button. Owner-only."
    ),
    "short_description": "Owner-only dashboard for the CLICKMINT partner network.",
    "commands": [("start", "Open the owner dashboard")],
}

ALL_BOTS = [REWARD, PARTNERSHIP, ADMIN]

# Profile images (Telegram crops them to a circle — see the branding doc).
LOGOS = {
    "reward": "docs/bot_reward_logo.png",
    "partnership": "docs/bot_partnership_logo.png",
    "admin": "docs/bot_admin_logo.png",
}


def validate(brand: dict) -> list[str]:
    """Return a list of limit violations (empty means the copy is postable)."""
    problems = []
    if len(brand["name"]) > NAME_LIMIT:
        problems.append(f"{brand['key']}: name is {len(brand['name'])} > {NAME_LIMIT}")
    if len(brand["description"]) > DESCRIPTION_LIMIT:
        problems.append(f"{brand['key']}: description is {len(brand['description'])} "
                        f"> {DESCRIPTION_LIMIT}")
    if len(brand["short_description"]) > SHORT_DESCRIPTION_LIMIT:
        problems.append(f"{brand['key']}: about is {len(brand['short_description'])} "
                        f"> {SHORT_DESCRIPTION_LIMIT}")
    return problems


def validate_all() -> list[str]:
    return [p for b in ALL_BOTS for p in validate(b)]


async def apply(brand: dict, token: str) -> None:
    """Push one bot's branding to Telegram (name, description, about, commands)."""
    from aiogram import Bot
    from aiogram.types import BotCommand

    bot = Bot(token=token)
    try:
        await bot.set_my_name(name=brand["name"])
        await bot.set_my_description(description=brand["description"])
        await bot.set_my_short_description(short_description=brand["short_description"])
        await bot.set_my_commands([BotCommand(command=c, description=d)
                                   for c, d in brand["commands"]])
    finally:
        await bot.session.close()


async def _main(check_only: bool) -> int:
    import config

    problems = validate_all()
    if problems:
        print("Branding copy exceeds Telegram's limits:")
        for p in problems:
            print("  •", p)
        return 1
    for brand in ALL_BOTS:
        token = getattr(config, brand["token_env"], "")
        print(f"[{brand['key']}] name={brand['name']!r} "
              f"about={len(brand['short_description'])}ch "
              f"description={len(brand['description'])}ch "
              f"commands={len(brand['commands'])} logo={LOGOS[brand['key']]}")
        if check_only:
            continue
        if not token or token.startswith("YOUR_"):
            print(f"  ! {brand['token_env']} is not set — skipped")
            continue
        await apply(brand, token)
        print("  ✔ applied")
    if check_only:
        print("\n--check: nothing was sent to Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main("--check" in sys.argv)))
