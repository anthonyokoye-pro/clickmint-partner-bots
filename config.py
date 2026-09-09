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
# Encryption key for user-owned Telegram bot credentials. Keep in a secrets
# manager; changing it intentionally invalidates stored credentials.
CLICKMINT_CREDENTIAL_KEY = env("CLICKMINT_CREDENTIAL_KEY", "")

# Your numeric Telegram user id — identifies the OWNER (splits owner/admin/user menus).
OWNER_USER_ID = int(env("OWNER_USER_ID", "0") or 0)

# Where the JSON store files live (default: the bot's working directory).
STORE_DIR = env("STORE_DIR", ".")
REWARD_STORE_PATH = os.path.join(STORE_DIR, "reward_ledger.json")
PARTNER_STORE_PATH = os.path.join(STORE_DIR, "partnership_state.json")
# Transactional Mint/referral foundation. This is separate from legacy JSON until
# the reward flows are migrated to the ledger in a later compatibility step.
MINT_DB_PATH = env("MINT_DB_PATH", os.path.join(STORE_DIR, "mint.sqlite3"))
# Marketplace state is isolated during the additive migration. It can later be
# moved into the authoritative platform database after the task schema is verified.
TASK_DB_PATH = env("TASK_DB_PATH", os.path.join(STORE_DIR, "tasks.sqlite3"))
CREDIBILITY_DB_PATH = env("CREDIBILITY_DB_PATH", os.path.join(STORE_DIR, "credibility.sqlite3"))
BROADCAST_DB_PATH = env("BROADCAST_DB_PATH", os.path.join(STORE_DIR, "broadcast.sqlite3"))
ADMIN_API_DB_PATH = env("ADMIN_API_DB_PATH", os.path.join(STORE_DIR, "admin_api.sqlite3"))
ENFORCEMENT_DB_PATH = env("ENFORCEMENT_DB_PATH", os.path.join(STORE_DIR, "enforcement.sqlite3"))
# Shared by Reward AND Partnership: one bot per ClickMint account, one destination truth.
VERIFICATION_DB_PATH = env("VERIFICATION_DB_PATH", os.path.join(STORE_DIR, "verification.sqlite3"))
ADS_DB_PATH = env("ADS_DB_PATH", os.path.join(STORE_DIR, "ads.sqlite3"))
# Advertising kill switch. OFF by default: consents/terms/drafts/review can be
# prepared, but nothing queues or delivers until the owner flips this on.
ADS_ENABLED = env("ADS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_ALLOWED_ORIGINS = [item.strip() for item in env("ADMIN_ALLOWED_ORIGINS", "").split(",") if item.strip()]
# Public HTTPS URL of the member onboarding Mini App (served by admin_server at
# /onboarding_web/). When unset the bots fall back to the in-chat /connectbot flow.
ONBOARDING_WEBAPP_URL = env("ONBOARDING_WEBAPP_URL", "").strip()
# A successful Telegram verification is cached briefly, then participation
# requires another real Bot API verification.
VERIFICATION_MAX_AGE_SECONDS = int(env("VERIFICATION_MAX_AGE_SECONDS", "86400") or 86400)
# Keep legacy mode until `python3 migrate_mint.py` has been run and the
# transactional backend has been verified in staging.
MINT_LEDGER_MODE = env("MINT_LEDGER_MODE", "legacy").strip().lower()
