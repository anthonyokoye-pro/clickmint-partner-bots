"""User-owned Telegram bot credentials and destination verification.

Telegram permission checks are made with the user's bot token.  A shared
CLICKMINT token cannot prove that an arbitrary user-owned bot is an
administrator, so this service deliberately keeps bot identity and destination
verification together.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, asdict


class CredentialError(ValueError):
    pass


class VerificationError(RuntimeError):
    pass


def _key() -> bytes:
    raw = os.environ.get("CLICKMINT_CREDENTIAL_KEY", "")
    if not raw:
        raise CredentialError("CLICKMINT_CREDENTIAL_KEY is not configured")
    return hashlib.sha256(raw.encode()).digest()


def _crypt_bytes(value: bytes, nonce: bytes, key: bytes) -> bytes:
    # HMAC-derived stream encryption; authenticated by the MAC below.  The key
    # is never stored with the record. Replace with a KMS/AEAD provider in
    # production deployments that have one available.
    out = bytearray(); counter = 0
    while len(out) < len(value):
        out.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(a ^ b for a, b in zip(value, out))


class BotCredentialStore:
    """Encrypted token records in the existing JsonStore."""
    def __init__(self, store, key: str = "telegram_bot_credentials"):
        self.store, self.key = store, key

    def save(self, owner_id: int, token: str, bot: dict) -> dict:
        if not token or ":" not in token:
            raise CredentialError("Telegram bot token is invalid")
        key = _key(); nonce = secrets.token_bytes(16)
        ciphertext = _crypt_bytes(token.encode(), nonce, key)
        mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        record = {
            "owner_id": int(owner_id), "bot_id": int(bot["id"]),
            "username": bot.get("username"), "name": bot.get("first_name"),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "mac": base64.b64encode(mac).decode(),
            "updated_at": int(time.time()),
        }
        items = self.store.get(self.key, {})
        items[str(owner_id)] = record
        self.store[self.key] = items; self.store.sync()
        return {k: v for k, v in record.items() if k not in {"nonce", "ciphertext", "mac"}}

    def token(self, owner_id: int) -> str:
        record = self.store.get(self.key, {}).get(str(owner_id))
        if not record:
            raise CredentialError("no Telegram bot is connected")
        key = _key()
        nonce = base64.b64decode(record["nonce"]); ciphertext = base64.b64decode(record["ciphertext"])
        actual = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(actual, base64.b64decode(record["mac"])):
            raise CredentialError("stored Telegram bot credential failed integrity validation")
        return _crypt_bytes(ciphertext, nonce, key).decode()

    def remove(self, owner_id: int) -> bool:
        items = self.store.get(self.key, {})
        existed = str(owner_id) in items
        items.pop(str(owner_id), None)
        self.store[self.key] = items; self.store.sync()
        return existed

    def public(self, owner_id: int) -> dict | None:
        record = self.store.get(self.key, {}).get(str(owner_id))
        if not record:
            return None
        return {k: record.get(k) for k in ("owner_id", "bot_id", "username", "name", "updated_at")}


@dataclass(frozen=True)
class VerificationResult:
    state: str
    eligible: bool
    chat_id: str
    chat_type: str = ""
    title: str = ""
    username: str | None = None
    member_status: str = "unknown"
    member_count: int | None = None
    checked_at: int = 0
    reasons: tuple[str, ...] = ()
    permissions: dict | None = None

    def as_dict(self):
        return asdict(self)


class TelegramVerificationService:
    def __init__(self, credential_store: BotCredentialStore, *, bot_factory=None):
        self.credentials = credential_store
        self.bot_factory = bot_factory

    async def connect_bot(self, owner_id: int, token: str) -> dict:
        if self.bot_factory is None:
            from aiogram import Bot
            bot = Bot(token=token)
        else:
            bot = self.bot_factory(token)
        try:
            me = await bot.get_me()
            data = me.model_dump() if hasattr(me, "model_dump") else dict(me)
            if not data.get("id") or not data.get("is_bot", True):
                raise CredentialError("Telegram did not return a bot identity")
            return self.credentials.save(owner_id, token, data)
        finally:
            session = getattr(bot, "session", None)
            close = getattr(session, "close", None)
            if close:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    async def verify(self, owner_id: int, chat_ref) -> VerificationResult:
        token = self.credentials.token(owner_id)
        if self.bot_factory is None:
            from aiogram import Bot
            bot = Bot(token=token)
        else:
            bot = self.bot_factory(token)
        checked = int(time.time())
        try:
            chat = await bot.get_chat(chat_ref)
            chat_data = chat.model_dump() if hasattr(chat, "model_dump") else dict(chat)
            chat_id = str(chat_data["id"]); chat_type = chat_data.get("type", "")
            member = await bot.get_chat_member(chat_data["id"], int(self.credentials.public(owner_id)["bot_id"]))
            member_data = member.model_dump() if hasattr(member, "model_dump") else dict(member)
            status = member_data.get("status", "unknown")
            reasons = []
            if status in {"left", "kicked"}:
                reasons.append("your bot is not a member of this destination")
            elif status not in {"administrator", "creator"}:
                reasons.append("your bot is not an administrator")
            permissions = {k: bool(member_data.get(k)) for k in (
                "can_post_messages", "can_edit_messages", "can_delete_messages", "can_pin_messages",
                "can_restrict_members", "can_invite_users") if k in member_data}
            if chat_type == "channel" and not permissions.get("can_post_messages", False):
                reasons.append("your bot lacks permission to post messages in this channel")
            if chat_type not in {"channel", "group", "supergroup"}:
                reasons.append("this Telegram chat type is not supported")
            count = None
            try:
                count = await bot.get_chat_member_count(chat_data["id"])
            except Exception:
                # Verification can pass while statistics are temporarily unavailable.
                pass
            state = "VERIFIED" if not reasons else ("DISCONNECTED" if status in {"left", "kicked"} else "DEGRADED")
            return VerificationResult(state, not reasons, chat_id, chat_type,
                                     chat_data.get("title", ""), chat_data.get("username"),
                                     status, count, checked, tuple(reasons), permissions)
        except Exception as exc:
            text = str(exc).lower()
            state = "INACCESSIBLE" if any(x in text for x in ("not found", "chat not found", "forbidden")) else "DEGRADED"
            return VerificationResult(state, False, str(chat_ref), checked_at=checked,
                                     reasons=("Telegram could not access this destination; add the bot as an administrator and try again.",))
        finally:
            session = getattr(bot, "session", None); close = getattr(session, "close", None)
            if close:
                result = close()
                if hasattr(result, "__await__"):
                    await result
