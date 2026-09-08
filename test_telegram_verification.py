"""Offline tests for user-owned Telegram bot credentials and verification."""
from __future__ import annotations

import asyncio
import os
import tempfile

from store import JsonStore
from telegram_verification import BotCredentialStore, TelegramVerificationService


class FakeBot:
    def __init__(self, token):
        self.token = token
        self.session = self

    async def close(self):
        return None

    async def get_me(self):
        return {"id": 77, "is_bot": True, "username": "owner_bot", "first_name": "Owner Bot"}

    async def get_chat(self, ref):
        return {"id": -1001, "type": "channel", "title": "Example", "username": "example"}

    async def get_chat_member(self, chat_id, user_id):
        return {"status": "administrator", "can_post_messages": True,
                "can_edit_messages": True, "can_delete_messages": True}

    async def get_chat_member_count(self, chat_id):
        return 1234


class FakeMemberFailure(FakeBot):
    async def get_chat_member(self, chat_id, user_id):
        return {"status": "member"}


def store():
    return JsonStore(os.path.join(tempfile.mkdtemp(), "state.json"))


def test_token_is_round_tripped_without_plaintext_record():
    os.environ["CLICKMINT_CREDENTIAL_KEY"] = "test-secret"
    s = store(); c = BotCredentialStore(s)
    c.save(10, "123:secret-token", {"id": 77, "username": "owner_bot"})
    assert c.token(10) == "123:secret-token"
    assert "secret-token" not in str(s.get("telegram_bot_credentials"))
    assert c.public(10)["bot_id"] == 77


def test_verification_requires_admin_post_permission():
    os.environ["CLICKMINT_CREDENTIAL_KEY"] = "test-secret"
    c = BotCredentialStore(store())
    c.save(10, "123:secret-token", {"id": 77, "username": "owner_bot"})
    service = TelegramVerificationService(c, bot_factory=FakeMemberFailure)
    result = asyncio.run(service.verify(10, "@example"))
    assert result.eligible is False
    assert result.state == "DEGRADED"
    assert any("administrator" in reason for reason in result.reasons)


def test_verification_retrieves_telegram_count():
    os.environ["CLICKMINT_CREDENTIAL_KEY"] = "test-secret"
    c = BotCredentialStore(store())
    c.save(10, "123:secret-token", {"id": 77, "username": "owner_bot"})
    service = TelegramVerificationService(c, bot_factory=FakeBot)
    result = asyncio.run(service.verify(10, "@example"))
    assert result.eligible is True
    assert result.state == "VERIFIED"
    assert result.member_count == 1234
    assert result.chat_type == "channel"


if __name__ == "__main__":
    for name in sorted(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("OK Telegram verification tests")
