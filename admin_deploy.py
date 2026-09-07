"""Deployment/integration checks for the Admin Mini App boundary."""
from __future__ import annotations

from pathlib import Path


def admin_preflight(root: str | Path, *, bot_token: str, owner_id: str,
                    allowed_origins: list[str]) -> list[str]:
    root = Path(root)
    errors = []
    if not bot_token or len(bot_token) < 20:
        errors.append("Telegram bot token is missing or malformed")
    try:
        if int(owner_id) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("OWNER_USER_ID must be a positive integer")
    required = ["admin_web/index.html", "admin_web/app.js", "admin_web/styles.css",
                "admin_api.py", "admin_http.py", "webapp_auth.py"]
    for relative in required:
        if not (root / relative).is_file():
            errors.append(f"missing Admin Mini App artifact: {relative}")
    if not allowed_origins or any(not origin.startswith("https://") for origin in allowed_origins):
        errors.append("ADMIN_ALLOWED_ORIGINS must contain HTTPS origins")
    return errors
