"""CLICKMINT configuration — read from ENVIRONMENT VARIABLES, never hardcoded.

Why: the code lives on GitHub. Tokens and the owner id are SECRETS and must NOT be
committed. Set them as env vars in your host / CI / secrets manager instead.

Provide a `.env` file locally (see .env.example) or export the vars on the server.
The PYTHON can read a .env with `dotenv` if you want:
    pip install python-dotenv   # optional
    from dotenv import load_dotenv; load_dotenv()
"""
import os

# Optionally load a local .env so you can keep secrets OUT of the repo but still
# run locally without exporting anything. Requires `pip install python-dotenv`.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# Per-bot tokens (from @BotFather) — set ONLY in your environment, never here.
REWARD_BOT_TOKEN = env("REWARD_BOT_TOKEN", "YOUR_REWARD_BOT_TOKEN")
PARTNER_BOT_TOKEN = env("PARTNER_BOT_TOKEN", "YOUR_PARTNER_BOT_TOKEN")
ADMIN_BOT_TOKEN = env("ADMIN_BOT_TOKEN", "YOUR_ADMIN_BOT_TOKEN")

# Your numeric Telegram user id — identifies the OWNER (splits owner/admin/user menus).
OWNER_USER_ID = int(env("OWNER_USER_ID", "0") or 0)

# Where the JSON store files live (default: the bot's working directory).
STORE_DIR = env("STORE_DIR", ".")
REWARD_STORE_PATH = os.path.join(STORE_DIR, "reward_ledger.json")
PARTNER_STORE_PATH = os.path.join(STORE_DIR, "partnership_state.json")
# Transactional Mint/referral foundation. This is separate from legacy JSON until
# the reward flows are migrated to the ledger in a later compatibility step.
MINT_DB_PATH = env("MINT_DB_PATH", os.path.join(STORE_DIR, "mint.sqlite3"))
