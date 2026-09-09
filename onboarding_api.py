"""Secure Web App onboarding of a member's Telegram bot token.

Why
---
`/connectbot <token>` puts a bot token into a Telegram chat. The bot tries to
delete that message, but deletion is best-effort (fails in some clients, after
48 h, or when the bot lacks rights) and the token has already crossed Telegram's
servers and any device with that chat open. That is the wrong channel for a
secret.

The Mini App path
-----------------
1. The bot sends a `web_app` button (HTTPS URL on the ClickMint origin).
2. The page collects the token in a password field and POSTs it directly to
   `/api/onboarding/connect` with `X-Telegram-Init-Data`.
3. The server validates `initData` (HMAC with the *reward bot's* token, age
   limit) — this is the only proof of who is connecting, and it is verified
   server-side; the page cannot spoof it.
4. The token is checked against Telegram (`getMe`) and stored encrypted in the
   shared verification DB under the initData user id. It is never echoed back,
   never logged, never stored in plain text, and never sent as a Telegram message.

Security properties
-------------------
* Identity: signed initData only; no user id is accepted from the body.
* Ownership: one bot per ClickMint account is enforced by the shared credential
  store (works across Reward and Partnership because the store is shared).
* Enforcement: banned/suspended users cannot connect (same gate as the bot).
* Transport: HTTPS origin allowlist, rate limiting and idempotency are inherited
  from `AdminWSGI`; the token is redacted from every error and audit path.
* Least privilege: this API is NOT admin-scoped. It exposes exactly three
  operations to the authenticated user about *their own* credential:
  status, connect, disconnect.
"""
from __future__ import annotations

import asyncio
import re
import time

from webapp_auth import validate_init_data, InitDataError

_TOKEN_RE = re.compile(r"^\d{4,12}:[A-Za-z0-9_-]{30,}$")


class OnboardingError(ValueError):
    pass


def redact(text: str) -> str:
    """Strip anything that looks like a bot token from a message before it can
    reach a log, an audit row or a response."""
    return re.sub(r"\d{4,12}:[A-Za-z0-9_-]{30,}", "<redacted-token>", str(text))


class OnboardingAPI:
    def __init__(self, *, platform_bot_token: str, credentials, verification, channels,
                 destination_states, enforcement_gate=None, audit=None, max_age: int = 600):
        self.platform_bot_token = platform_bot_token
        self.credentials = credentials              # BotCredentialStore (shared DB)
        self.verification = verification            # TelegramVerificationService
        self.channels = channels
        self.destination_states = destination_states
        self.enforcement_gate = enforcement_gate
        self.audit = audit                          # callable(actor_id, action, reason) | None
        self.max_age = int(max_age)                 # onboarding is a short, deliberate action

    # ------------------------------------------------------------ identity
    def authenticate(self, init_data: str):
        try:
            return validate_init_data(init_data, self.platform_bot_token, max_age=self.max_age)
        except InitDataError as exc:
            raise PermissionError(str(exc)) from exc

    def _gate(self, user_id: int):
        if self.enforcement_gate is None:
            return
        decision = self.enforcement_gate.check_many([(user_id, "user")], "registration")
        if not decision:
            raise PermissionError(self.enforcement_gate.block_message(decision))

    def _record(self, actor_id: int, action: str, reason: str):
        if self.audit is not None:
            try:
                self.audit(actor_id, action, redact(reason)[:200])
            except Exception:
                pass

    # ---------------------------------------------------------- operations
    def status(self, init_data: str) -> dict:
        identity = self.authenticate(init_data)
        public = self.credentials.public(identity.user_id)
        destinations = [{"chat_id": r.get("chat_id"), "username": r.get("username"),
                         "status": r.get("status"), "verified_state": r.get("verified_state")}
                        for r in self.channels.mine(identity.user_id)]
        return {"user_id": identity.user_id, "connected": bool(public),
                "bot": public, "destinations": destinations}

    def connect(self, init_data: str, token: str) -> dict:
        identity = self.authenticate(init_data)
        self._gate(identity.user_id)
        token = (token or "").strip()
        if not _TOKEN_RE.match(token):
            raise OnboardingError("that does not look like a BotFather token")
        previous = self.credentials.public(identity.user_id)
        try:
            result = asyncio.run(self.verification.connect_bot(identity.user_id, token))
        except RuntimeError as exc:
            # Already inside a loop (tests / embedded): run on a private loop.
            if "asyncio.run() cannot be called" not in str(exc):
                raise
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(self.verification.connect_bot(identity.user_id, token))
            finally:
                loop.close()
        except Exception as exc:
            self._record(identity.user_id, "BOT_CONNECT_FAILED", str(exc))
            raise OnboardingError(redact(str(exc))) from exc
        changed = bool(previous and int(previous.get("bot_id", -1)) != int(result.get("bot_id", -2)))
        if changed:
            from destination_state import VState, IllegalTransition
            for row in self.channels.mine(identity.user_id):
                try:
                    self.destination_states.transition(
                        identity.user_id, row.get("chat_id") or row.get("username"), VState.DISCONNECTED,
                        reason="connected bot changed via Web App; re-verify", source="owner",
                        verification_reasons=["Connected bot changed; destination must be re-verified"])
                except IllegalTransition:
                    pass
        self._record(identity.user_id, "BOT_CONNECTED", f"bot_id={result.get('bot_id')} changed={changed} via=webapp")
        return {"connected": True, "bot": {k: result.get(k) for k in ("bot_id", "username", "name", "updated_at")},
                "bot_changed": changed, "at": int(time.time())}

    def disconnect(self, init_data: str) -> dict:
        identity = self.authenticate(init_data)
        existed = self.credentials.remove(identity.user_id)
        from destination_state import VState, IllegalTransition
        for row in self.channels.mine(identity.user_id):
            try:
                self.destination_states.transition(
                    identity.user_id, row.get("chat_id") or row.get("username"), VState.DISCONNECTED,
                    reason="bot disconnected via Web App", source="owner",
                    verification_reasons=["Bot disconnected by owner"])
            except IllegalTransition:
                pass
        self._record(identity.user_id, "BOT_DISCONNECTED", "via=webapp")
        return {"connected": False, "removed": existed}
