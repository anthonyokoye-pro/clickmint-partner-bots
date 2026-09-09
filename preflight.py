"""CLICKMINT — preflight check before you start the bots for real.

Answers one question: "are my secrets set up correctly?" It validates every value
config.py reads, then (unless you pass --offline) asks Telegram to confirm each
token is live and prints the @username it belongs to.

    python3 preflight.py             # validate + confirm the tokens with Telegram
    python3 preflight.py --offline   # validate the local setup only, no network

Nothing is ever printed in full: tokens are masked. Exits non-zero if anything is
wrong, so you can put it in a deploy script.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys

import config
from console import configure as configure_console

# Windows consoles may default to a legacy code page that cannot print the
# status symbols below. Configure streams before emitting any diagnostics.
configure_console()

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
PLACEHOLDERS = {"", "YOUR_REWARD_BOT_TOKEN", "YOUR_PARTNER_BOT_TOKEN",
                "YOUR_ADMIN_BOT_TOKEN"}

BOTS = [
    ("REWARD_BOT_TOKEN", config.REWARD_BOT_TOKEN, "reward_bot.py"),
    ("PARTNER_BOT_TOKEN", config.PARTNER_BOT_TOKEN, "partnership_bot.py"),
    ("ADMIN_BOT_TOKEN", config.ADMIN_BOT_TOKEN, "admin_bot.py"),
]

ok = True


def good(msg: str):
    print(f"  \033[32m✓\033[0m {msg}")


def bad(msg: str, fix: str = ""):
    global ok
    ok = False
    print(f"  \033[31m✗\033[0m {msg}")
    if fix:
        print(f"      → {fix}")


def mask(tok: str) -> str:
    if ":" not in tok:
        return "(not a token)"
    head, tail = tok.split(":", 1)
    return f"{head}:{tail[:4]}…{tail[-2:]}"


def check_local() -> None:
    print("\n\033[1m1. Secrets\033[0m")
    if os.path.exists(".env"):
        good(".env found (config.py loads it via python-dotenv)")
        if importlib.util.find_spec("dotenv") is None:
            bad("python-dotenv is NOT installed, so .env will be ignored",
                "pip install -r requirements.txt")
    else:
        print("  \033[2m·\033[0m no .env file — reading the environment directly "
              "(fine on a server)")

    for name, tok, script in BOTS:
        if tok in PLACEHOLDERS:
            bad(f"{name} is not set", f"add {name}=... to .env (needed by {script})")
        elif not TOKEN_RE.match(tok):
            bad(f"{name} doesn't look like a BotFather token: {mask(tok)}",
                "expected the form 1234567890:AA... — re-copy it from @BotFather")
        else:
            good(f"{name} = {mask(tok)}")

    seen: dict[str, str] = {}
    for name, tok, _ in BOTS:
        if TOKEN_RE.match(tok):
            if tok in seen:
                bad(f"{name} is the SAME token as {seen[tok]}",
                    "each bot needs its own token, or they will fight over updates")
            seen[tok] = name

    print("\n\033[1m2. Owner\033[0m")
    if not config.OWNER_USER_ID:
        bad("OWNER_USER_ID is 0 — nobody is the owner",
            "get your numeric id from @userinfobot and set OWNER_USER_ID")
    elif config.OWNER_USER_ID < 0:
        bad(f"OWNER_USER_ID={config.OWNER_USER_ID} is negative (that's a chat id, "
            "not a user id)", "a user id is a positive number")
    else:
        good(f"OWNER_USER_ID = {config.OWNER_USER_ID} — exempt from credits/caps, "
             "sole grantor of admin invites")

    print("\n\033[1m3. User-owned bot credential security\033[0m")
    if not config.CLICKMINT_CREDENTIAL_KEY or len(config.CLICKMINT_CREDENTIAL_KEY) < 32:
        bad("CLICKMINT_CREDENTIAL_KEY is missing or too short",
            "set a random secret of at least 32 characters in the deployment secret manager")
    else:
        good("CLICKMINT_CREDENTIAL_KEY is configured")

    print("\n\033[1m4. Verification policy\033[0m")
    if config.VERIFICATION_MAX_AGE_SECONDS <= 0:
        bad("VERIFICATION_MAX_AGE_SECONDS must be positive",
            "set it to a positive number of seconds, such as 86400")
    else:
        good(f"Verification freshness = {config.VERIFICATION_MAX_AGE_SECONDS} seconds")

    print("\n\033[1m5. Storage\033[0m")
    store_dir = os.path.abspath(config.STORE_DIR)
    if not os.path.isdir(store_dir):
        bad(f"STORE_DIR does not exist: {store_dir}", f"mkdir -p {store_dir}")
    elif not os.access(store_dir, os.W_OK):
        bad(f"STORE_DIR is not writable: {store_dir}",
            "the bots must be able to write their JSON store there")
    else:
        good(f"STORE_DIR = {store_dir} (writable)")
        for path in (config.REWARD_STORE_PATH, config.PARTNER_STORE_PATH):
            state = "exists — data will be reused" if os.path.exists(path) \
                else "will be created on first run"
            print(f"      \033[2m{os.path.basename(path)}: {state}\033[0m")


async def check_live() -> None:
    print("\n\033[1m6. Telegram\033[0m")
    try:
        from aiogram import Bot
        import cryptography  # noqa: F401
    except ImportError as exc:
        bad(f"required dependency is not installed: {exc.name or exc}",
            "pip install -r requirements.txt")
        return

    for name, tok, script in BOTS:
        if not TOKEN_RE.match(tok):
            continue
        bot = Bot(token=tok)
        try:
            me = await bot.get_me()
            good(f"{name} → @{me.username} ({me.first_name}) — {script}")
        except Exception as exc:                        # noqa: BLE001
            msg = str(exc)
            if "Unauthorized" in msg:
                bad(f"{name} was REJECTED by Telegram",
                    "the token is wrong or was revoked — /revoke then /token in "
                    "@BotFather")
            else:
                bad(f"{name}: could not reach Telegram ({type(exc).__name__}: "
                    f"{msg[:80]})",
                    "check the machine's internet access, then re-run")
        finally:
            await bot.session.close()


def main() -> int:
    print("\033[1mCLICKMINT preflight\033[0m")
    check_local()
    if "--offline" not in sys.argv:
        asyncio.run(check_live())
    else:
        print("\n\033[2m(skipping the Telegram check: --offline)\033[0m")

    print()
    if ok:
        print("\033[32m\033[1mREADY.\033[0m Start them with:")
        print("  python3 reward_bot.py   ·   python3 partnership_bot.py   ·   "
              "python3 admin_bot.py")
        return 0
    print("\033[31m\033[1mNOT READY\033[0m — fix the ✗ items above, then re-run "
          "python3 preflight.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
