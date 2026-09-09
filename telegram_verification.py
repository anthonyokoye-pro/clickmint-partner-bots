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

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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
        key = _key(); nonce = secrets.token_bytes(12)
        aad = f"clickmint:telegram-bot:{int(owner_id)}".encode()
        ciphertext = AESGCM(key).encrypt(nonce, token.encode(), aad)
        items = self.store.get(self.key, {})
        bot_id = int(bot["id"])
        for existing_owner, existing in items.items():
            if str(existing_owner) != str(owner_id) and int(existing.get("bot_id", -1)) == bot_id:
                raise CredentialError("this Telegram bot is already connected to another ClickMint account")
        record = {
            "owner_id": int(owner_id), "bot_id": bot_id,
            "username": bot.get("username"), "name": bot.get("first_name"),
            "encryption": "aes-gcm-v1",
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
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
        nonce = base64.b64decode(record["nonce"])
        ciphertext = base64.b64decode(record["ciphertext"])
        try:
            if record.get("encryption") == "aes-gcm-v1":
                aad = f"clickmint:telegram-bot:{int(owner_id)}".encode()
                return AESGCM(key).decrypt(nonce, ciphertext, aad).decode()
            # Legacy records are read-only compatible and should be rotated by
            # reconnecting the bot; they remain authenticated by their HMAC.
            actual = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(actual, base64.b64decode(record["mac"])):
                raise CredentialError("stored Telegram bot credential failed integrity validation")
            return _crypt_bytes(ciphertext, nonce, key).decode()
        except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise CredentialError("stored Telegram bot credential failed integrity validation") from exc

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
    # Each key corresponds to a real Bot API operation or policy evaluation.
    checks: dict | None = None

    def as_dict(self):
        return asdict(self)


def _failed_checks(*, destination: str = "failed", eligibility: str = "failed") -> dict:
    return {
        "destination": destination,
        "bot_membership": "unavailable",
        "administrator": "unavailable",
        "permissions": "unavailable",
        "accessibility": "failed" if destination == "failed" else "unavailable",
        "member_count": "unavailable",
        "eligibility": eligibility,
    }


class TelegramVerificationService:
    def __init__(self, credential_store: BotCredentialStore, *, bot_factory=None):
        self.credentials = credential_store
        self.bot_factory = bot_factory

    def make_bot(self, token: str):
        """Build an aiogram Bot for a user-owned token (mockable via bot_factory)."""
        if self.bot_factory is None:
            from aiogram import Bot
            return Bot(token=token)
        return self.bot_factory(token)

    async def connect_bot(self, owner_id: int, token: str) -> dict:
        bot = self.make_bot(token)
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
            checks = {
                "destination": "passed",
                "bot_membership": "passed" if status not in {"left", "kicked", "unknown"} else "failed",
                "administrator": "passed" if status in {"administrator", "creator"} else "failed",
                "permissions": "passed" if not any("permission" in reason for reason in reasons) else "failed",
                "accessibility": "passed",
                "member_count": "passed" if count is not None else "unavailable",
                "eligibility": "passed" if not reasons else "failed",
            }
            return VerificationResult(state, not reasons, chat_id, chat_type,
                                     chat_data.get("title", ""), chat_data.get("username"),
                                     status, count, checked, tuple(reasons), permissions, checks)
        except Exception as exc:
            text = str(exc).lower()
            if any(x in text for x in ("unauthorized", "invalid token", "token is invalid")):
                return VerificationResult("REVOKED", False, str(chat_ref), checked_at=checked,
                                         reasons=("Telegram rejected this bot token; reconnect the bot with /connectbot.",),
                                         checks=_failed_checks(destination="unavailable"))
            state = "INACCESSIBLE" if any(x in text for x in ("not found", "chat not found", "forbidden")) else "DEGRADED"
            reason = ("Telegram could not access this destination; add the bot as an administrator and try again."
                      if state == "INACCESSIBLE" else
                      "Telegram is temporarily unavailable; retry verification.")
            return VerificationResult(state, False, str(chat_ref), checked_at=checked,
                                     reasons=(reason,), checks=_failed_checks())
        finally:
            session = getattr(bot, "session", None); close = getattr(session, "close", None)
            if close:
                result = close()
                if hasattr(result, "__await__"):
                    await result
