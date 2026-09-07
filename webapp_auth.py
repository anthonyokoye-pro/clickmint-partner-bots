"""Server-side Telegram Mini App initData validation.

Client-provided user identity is never trusted until this validator succeeds.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramWebAppIdentity:
    user_id: int
    username: str | None
    auth_date: int
    query_id: str | None


def validate_init_data(init_data: str, bot_token: str, *, now: int | None = None,
                       max_age: int = 86400) -> TelegramWebAppIdentity:
    if not init_data or not bot_token:
        raise InitDataError("initData and bot token are required")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash or len(received_hash) != 64:
        raise InitDataError("missing or malformed initData hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataError("invalid initData signature")
    try:
        auth_date = int(data["auth_date"])
        if auth_date <= 0:
            raise ValueError
        current = int(now if now is not None else time.time())
        if current - auth_date < 0 or current - auth_date > max_age:
            raise InitDataError("initData is expired or from the future")
        user = json.loads(data["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, InitDataError):
            raise
        raise InitDataError("invalid Telegram user or auth_date") from exc
    return TelegramWebAppIdentity(
        user_id=user_id,
        username=user.get("username"),
        auth_date=auth_date,
        query_id=data.get("query_id"),
    )
