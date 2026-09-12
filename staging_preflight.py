"""Validate an isolated ClickMint staging environment without contacting Telegram.

Usage:
    python staging_preflight.py --env-file .env.staging

This checks configuration separation and never prints tokens or encryption keys.
Use staging_telegram_check.py separately for read-only Bot API validation.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlparse

TOKENS = ("REWARD_BOT_TOKEN", "PARTNER_BOT_TOKEN", "ADMIN_BOT_TOKEN")
PATHS = ("STORE_DIR", "MINT_DB_PATH", "TASK_DB_PATH", "BROADCAST_DB_PATH",
         "VERIFICATION_DB_PATH", "PLATFORM_DB_PATH", "CREDIBILITY_DB_PATH",
         "ENFORCEMENT_DB_PATH", "ADS_DB_PATH", "ADMIN_API_DB_PATH", "WORKER_DB_PATH")
FINANCIAL = ("DEPOSITS_ENABLED", "WITHDRAWALS_ENABLED", "CRYPTO_PAYMENTS_ENABLED",
             "FIAT_PAYMENTS_ENABLED", "TELEGRAM_STARS_ENABLED", "BOOST_PURCHASES_ENABLED",
             "EXTERNAL_PAYOUTS_ENABLED", "CURRENCY_CONVERSION_ENABLED")
_TOKEN = re.compile(r"^\d{4,12}:[A-Za-z0-9_-]{30,}$")


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate(values: dict[str, str]) -> list[str]:
    errors = []
    if values.get("ENVIRONMENT") != "staging":
        errors.append("ENVIRONMENT must be exactly staging")
    for key in TOKENS:
        token = values.get(key, "")
        if not _TOKEN.match(token):
            errors.append(f"{key} is missing or is not a BotFather token")
        if "staging" in token.lower() or "replace" in token.lower():
            errors.append(f"{key} still contains a placeholder")
    tokens = [values.get(key) for key in TOKENS]
    if len(set(tokens)) != len(tokens):
        errors.append("the three staging bot tokens must be different")
    if not values.get("OWNER_USER_ID", "").isdigit() or int(values["OWNER_USER_ID"]) <= 0:
        errors.append("OWNER_USER_ID must be a positive numeric Telegram user id")
    key = values.get("CLICKMINT_CREDENTIAL_KEY", "")
    if len(key) < 32 or "replace" in key.lower():
        errors.append("CLICKMINT_CREDENTIAL_KEY must be a new random secret of at least 32 characters")
    origins = [item.strip() for item in values.get("ADMIN_ALLOWED_ORIGINS", "").split(",") if item.strip()]
    if not origins or any(urlparse(item).scheme != "https" for item in origins):
        errors.append("ADMIN_ALLOWED_ORIGINS must contain at least one HTTPS origin")
    onboarding = values.get("ONBOARDING_WEBAPP_URL", "")
    if urlparse(onboarding).scheme != "https" or not any(onboarding.startswith(origin.rstrip("/") + "/") for origin in origins):
        errors.append("ONBOARDING_WEBAPP_URL must be HTTPS and belong to an allowed staging origin")
    missing_paths = [key for key in PATHS if not values.get(key)]
    if missing_paths:
        errors.append("all staging database paths must be configured: " + ", ".join(missing_paths))
    else:
        store = Path(values["STORE_DIR"]).resolve()
        if "staging" not in str(store).lower():
            errors.append("STORE_DIR must be a dedicated staging directory")
        for key in PATHS[1:]:
            if not Path(values[key]).resolve().is_relative_to(store):
                errors.append(f"{key} must be inside STORE_DIR")
    for key in FINANCIAL:
        if values.get(key, "false").lower() in {"1", "true", "yes", "on"}:
            errors.append(f"{key} must remain false in staging")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env.staging"))
    args = parser.parse_args()
    if not args.env_file.is_file():
        print(f"ERROR: {args.env_file} does not exist; copy .env.staging.example first")
        return 2
    try:
        errors = validate(load_env(args.env_file))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2
    if errors:
        print("STAGING PREFLIGHT FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("STAGING PREFLIGHT PASSED: isolated resources, HTTPS origin, unique bot tokens, and financial features disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
