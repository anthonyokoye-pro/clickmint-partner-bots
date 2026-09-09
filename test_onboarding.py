"""Secure Web App bot-token onboarding: identity from signed initData only,
token never echoed, ownership shared, HTTP boundary redacts secrets."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import tempfile
import time
from urllib.parse import urlencode

os.environ.setdefault("CLICKMINT_CREDENTIAL_KEY", "test-only-credential-key-please-change")

from store import JsonStore
from verification_store import VerificationStore
from channel_registry import ChannelRegistry
from telegram_verification import BotCredentialStore, TelegramVerificationService
from destination_state import DestinationStateMachine, VState
from onboarding_api import OnboardingAPI, OnboardingError, redact
from admin_http import AdminWSGI

PLATFORM = "111111:PLATFORMTOKENPLATFORMTOKENPLATFORMTOKEN"
GOOD = "9001:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def signed(token, values):
    check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode(values) + "&hash=" + hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_for(uid, token=PLATFORM, age=0):
    return signed(token, {"auth_date": str(int(time.time()) - age), "query_id": "q",
                          "user": json.dumps({"id": uid, "username": f"u{uid}"})})


class FakeBot:
    """Stands in for aiogram.Bot(token): getMe answers from the token prefix."""
    def __init__(self, token):
        self.token = token
        self.session = self
    async def get_me(self):
        class Me:
            def model_dump(_):
                return {"id": int(self.token.split(":")[0]), "is_bot": True, "username": f"bot{self.token.split(':')[0]}"}
        return Me()
    async def close(self):
        pass


class Gate:
    def __init__(self, blocked=()):
        self.blocked = set(blocked)
    def check_many(self, subjects, capability, **kw):
        class D:
            def __init__(s, ok): s.ok = ok
            def __bool__(s): return s.ok
        return D(not any(uid in self.blocked for uid, _ in subjects))
    def block_message(self, decision):
        return "your account is restricted"


def build():
    d = tempfile.mkdtemp(prefix="cm-onb-")
    vs = VerificationStore(os.path.join(d, "v.sqlite3"), legacy=JsonStore(os.path.join(d, "l.json")))
    creds = BotCredentialStore(vs)
    verification = TelegramVerificationService(creds, bot_factory=FakeBot)
    channels = ChannelRegistry(vs)
    events = []
    api = OnboardingAPI(platform_bot_token=PLATFORM, credentials=creds, verification=verification,
                        channels=channels, destination_states=DestinationStateMachine(channels),
                        enforcement_gate=Gate(blocked={666}),
                        audit=lambda a, act, r: events.append((a, act, r)))
    return api, creds, channels, events, vs


def test_identity_comes_only_from_signed_init_data():
    api, creds, *_ = build()
    for bad in ("", "user=%7B%22id%22%3A1%7D&hash=" + "0" * 64, init_for(1001, token="222222:WRONGTOKENWRONGTOKENWRONGTOKENWRONG"),
                init_for(1001, age=3600)):
        try:
            api.connect(bad, GOOD); raise AssertionError("accepted bad initData")
        except PermissionError:
            pass
    assert creds.public(1001) is None
    print("PASS unsigned / foreign-bot / stale initData is rejected before any token is touched")


def test_connect_stores_encrypted_and_never_echoes():
    api, creds, channels, events, vs = build()
    out = api.connect(init_for(1001), GOOD)
    assert out["connected"] and out["bot"]["bot_id"] == 9001 and out["bot"]["username"] == "bot9001"
    assert GOOD not in json.dumps(out)
    assert creds.token(1001) == GOOD
    raw = vs._conn.execute("SELECT record FROM bot_credentials").fetchone()[0]
    assert GOOD not in raw and "AAAAAAAA" not in raw, "token must be encrypted at rest"
    assert all(GOOD not in r for _, _, r in events)
    st = api.status(init_for(1001))
    assert st["connected"] and "ciphertext" not in json.dumps(st) and GOOD not in json.dumps(st)
    print("PASS token is validated with Telegram, stored encrypted, and never returned or audited")


def test_bot_change_disconnects_destinations_and_ownership_is_exclusive():
    api, creds, channels, *_ = build()
    api.connect(init_for(1001), GOOD)
    channels.add(1001, "@alice", "@alice", "channel", ["General"], size=5, bot_added=True)
    sm = DestinationStateMachine(channels)
    assert channels.get("@alice")["status"] == "ACTIVE"
    out = api.connect(init_for(1001), "9002:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
    assert out["bot_changed"] and channels.get("@alice")["status"] == "DISCONNECTED"
    assert channels.get("@alice")["state_history"][-1]["source"] == "owner"
    try:
        api.connect(init_for(2002), "9002:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"); raise AssertionError("shared bot")
    except OnboardingError as exc:
        assert "another ClickMint account" in str(exc)
    assert api.disconnect(init_for(1001))["removed"] is True and creds.public(1001) is None
    print("PASS changing bots disconnects destinations; a bot belongs to exactly one account")


def test_enforcement_and_format_guards():
    api, *_ = build()
    try:
        api.connect(init_for(666), GOOD); raise AssertionError("banned user connected")
    except PermissionError as exc:
        assert "restricted" in str(exc)
    for bad in ("", "not-a-token", "123:short", "abc:" + "A" * 40):
        try:
            api.connect(init_for(1001), bad); raise AssertionError(bad)
        except OnboardingError:
            pass
    assert "<redacted-token>" in redact(f"boom {GOOD} boom") and GOOD not in redact(f"x {GOOD}")
    print("PASS enforcement gate applies; malformed tokens are refused; redaction works")


def test_http_boundary():
    api, creds, *_ = build()
    app = AdminWSGI(object(), allowed_origins=None, onboarding=api)

    def call(method, path, body=None, init=None, ctype="application/json"):
        result = {}
        raw = json.dumps(body).encode() if body is not None else b""
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "HTTP_X_TELEGRAM_INIT_DATA": init or "",
               "CONTENT_TYPE": ctype, "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw), "REMOTE_ADDR": "1.2.3.4"}
        out = app(env, lambda s, h: result.update(status=s, headers=h))
        text = b"".join(out).decode()
        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError:
            payload = {"_raw": text}
        return result["status"], payload, dict(result["headers"])

    s, data, _ = call("GET", "/api/onboarding/status")
    assert s == "403 Forbidden", (s, data)
    s, data, _ = call("POST", "/api/onboarding/connect", {"token": GOOD}, init_for(1001), ctype="text/plain")
    assert s == "415 Unsupported Media Type"
    s, data, _ = call("POST", "/api/onboarding/connect", {"token": GOOD}, init_for(1001))
    assert s == "200 OK" and data["connected"] and GOOD not in json.dumps(data)
    s, data, _ = call("POST", "/api/onboarding/connect", {"token": GOOD}, init_for(2002))
    assert s == "400 Bad Request" and GOOD not in json.dumps(data) and "another" in data["error"]
    s, data, _ = call("GET", "/api/onboarding/status", init=init_for(1001))
    assert s == "200 OK" and data["bot"]["bot_id"] == 9001
    # The page is served with no-store and never from outside its directory.
    s, _, headers = call("GET", "/onboarding_web/index.html")
    assert s == "200 OK" and headers.get("Cache-Control") == "no-store"
    s, _, _ = call("GET", "/onboarding_web/../admin_http.py")
    assert s == "404 Not Found"
    # Without an onboarding object the routes do not exist at all.
    bare = AdminWSGI(object(), allowed_origins=None)
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/onboarding/status", "REMOTE_ADDR": "x"}
    res = {}; bare(env, lambda s, h: res.update(status=s))
    assert res["status"] == "404 Not Found"
    print("PASS HTTP boundary: 403 without identity, 415 wrong type, redacted errors, no-store page")


TESTS = [test_identity_comes_only_from_signed_init_data, test_connect_stores_encrypted_and_never_echoes,
         test_bot_change_disconnects_destinations_and_ownership_is_exclusive, test_enforcement_and_format_guards,
         test_http_boundary]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print(f"\nALL ONBOARDING TESTS PASSED ({len(TESTS)})")
