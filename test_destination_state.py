"""State-machine + Telegram error classification tests (pure, offline)."""
import tempfile
from pathlib import Path

from channel_registry import ChannelRegistry
from destination_state import (DestinationStateMachine, IllegalTransition, TelegramErrorKind, VState,
                               apply_verification_result, backoff_seconds, classify_telegram_error,
                               coerce_state, failed_result_from_reason)
from store import JsonStore
from telegram_verification import VerificationResult


def _registry():
    d = tempfile.TemporaryDirectory(prefix="clickmint-ds-")
    reg = ChannelRegistry(JsonStore(str(Path(d.name) / "s.json")))
    reg.add(1, "@c", "@c", "channel", ["General"])
    return d, reg


def test_legal_path_and_derived_status():
    d, reg = _registry()
    with d:
        clock = [1000]
        m = DestinationStateMachine(reg, clock=lambda: clock[0])
        assert m.state_of("@c") == VState.REGISTERED and reg.get("@c")["status"] == "REGISTERED"
        m.transition(1, "@c", VState.VERIFYING, reason="register", source="register")
        t = m.transition(1, "@c", VState.VERIFIED, reason="ok", source="register", size=120)
        row = reg.get("@c")
        assert t.changed and row["status"] == "ACTIVE" and row["bot_added"] is True
        assert row["size"] == 120 and row["last_verified_at"] == 1000
        # healthy recheck = self-transition, refreshes timestamp, no new history entry
        clock[0] = 2000
        t2 = m.transition(1, "@c", VState.VERIFIED, reason="recheck", source="background", last_verified_at=2000)
        assert not t2.changed and reg.get("@c")["last_verified_at"] == 2000
        assert [h["new"] for h in reg.get("@c")["state_history"]] == ["VERIFYING", "VERIFIED"]
        m.transition(1, "@c", "DEGRADED", reason="lost rights", source="scan")
        assert reg.get("@c")["status"] == "DEGRADED" and reg.get("@c")["bot_added"] is False


def test_illegal_transitions_are_rejected():
    d, reg = _registry()
    with d:
        m = DestinationStateMachine(reg)
        for bad in (VState.VERIFIED, VState.DEGRADED, VState.INACCESSIBLE):
            try:
                m.transition(1, "@c", bad, reason="skip verifying", source="test")
            except IllegalTransition:
                pass
            else:
                raise AssertionError(f"REGISTERED -> {bad} accepted")
        try:
            m.transition(1, "@c", VState.VERIFYING, reason="", source="test")
        except IllegalTransition:
            pass
        else:
            raise AssertionError("empty reason accepted")
        m.transition(1, "@c", VState.REMOVED, reason="owner removed", source="owner")
        try:
            m.transition(1, "@c", VState.VERIFYING, reason="resurrect", source="test")
        except IllegalTransition:
            pass
        else:
            raise AssertionError("REMOVED is terminal")
        assert coerce_state("ACTIVE") == VState.VERIFIED and coerce_state("garbage") == VState.REGISTERED


def test_recheck_schedule():
    d, reg = _registry()
    with d:
        m = DestinationStateMachine(reg, clock=lambda: 100_000)
        m.transition(1, "@c", VState.VERIFYING, reason="r", source="t")
        m.transition(1, "@c", VState.VERIFIED, reason="r", source="t")
        row = reg.get("@c")
        assert m.due_for_recheck([row], now=100_000 + 3600) == []
        assert m.due_for_recheck([row], now=100_000 + 7 * 3600) == [row]
        m.transition(1, "@c", VState.REVOKED, reason="token gone", source="t")
        assert m.due_for_recheck([reg.get("@c")], now=10 ** 9) == [], "REVOKED never auto-rechecks"


def test_apply_verification_result_end_to_end():
    d, reg = _registry()
    with d:
        m = DestinationStateMachine(reg, clock=lambda: 5000)
        m.transition(1, "@c", VState.VERIFYING, reason="r", source="register")
        changes = []
        ok, why = apply_verification_result(
            m, reg.get("@c"),
            VerificationResult("VERIFIED", True, "-100", "channel", "T", "c", "administrator", 321, 5000, (), {"can_post_messages": True}, {}),
            source="register", on_change=lambda r, p, n, why: changes.append((p, n)))
        assert ok and changes == [("VERIFYING", "ACTIVE")]
        row = reg.get("@c")
        assert row["telegram_member_count"] == 321 and row["canonical_chat_id"] == "-100"
        ok, why = apply_verification_result(
            m, reg.get("@c"),
            VerificationResult("DISCONNECTED", False, "-100", checked_at=6000, reasons=("your bot is not a member",), error_kind="bot_kicked"),
            source="background", on_change=lambda r, p, n, why: changes.append((p, n)))
        assert not ok and changes[-1] == ("ACTIVE", "DISCONNECTED")
        assert reg.get("@c")["last_error_kind"] == "bot_kicked"
        # wrapping a non-Telegram failure
        assert failed_result_from_reason(reg.get("@c"), "connect your bot").state == "REVOKED"
        assert failed_result_from_reason(reg.get("@c"), "destination has no Telegram chat id") is None


def test_error_classification():
    class TelegramUnauthorizedError(Exception): ...
    class TelegramRetryAfter(Exception):
        retry_after = 17
    class TelegramForbiddenError(Exception): ...

    cases = [
        (TelegramUnauthorizedError("Unauthorized"), TelegramErrorKind.TOKEN_REVOKED, True, VState.REVOKED),
        (Exception("Telegram server says - Bad Request: chat not found"), TelegramErrorKind.CHAT_NOT_FOUND, True, VState.INACCESSIBLE),
        (TelegramForbiddenError("Forbidden: bot was kicked from the channel chat"), TelegramErrorKind.BOT_KICKED, True, VState.DISCONNECTED),
        (Exception("Bad Request: need administrator rights in the channel chat"), TelegramErrorKind.NOT_ENOUGH_RIGHTS, True, VState.DEGRADED),
        (Exception("Forbidden: user is deactivated"), TelegramErrorKind.USER_DEACTIVATED, True, None),
        (TelegramRetryAfter("Too Many Requests: retry after 17"), TelegramErrorKind.FLOOD, False, None),
        (Exception("Bad Request: message is too long"), TelegramErrorKind.MESSAGE_INVALID, True, None),
        (Exception("Cannot connect to host api.telegram.org:443"), TelegramErrorKind.NETWORK, False, None),
        (Exception("Bad Gateway"), TelegramErrorKind.SERVER, False, None),
        (Exception("something odd"), TelegramErrorKind.UNKNOWN, False, None),
    ]
    for exc, kind, permanent, vstate in cases:
        info = classify_telegram_error(exc)
        assert info.kind == kind, (exc, info.kind)
        assert info.permanent is permanent and info.verification_state == vstate, (exc, info)
    flood = classify_telegram_error(TelegramRetryAfter("retry after 17"))
    assert flood.retry_after == 17 and backoff_seconds(5, retry_after=flood.retry_after) == 17
    assert backoff_seconds(0) == 30 and backoff_seconds(3) == 240 and backoff_seconds(50) == 3600


if __name__ == "__main__":
    for t in (test_legal_path_and_derived_status, test_illegal_transitions_are_rejected, test_recheck_schedule,
              test_apply_verification_result_end_to_end, test_error_classification):
        t()
        print(f"PASS {t.__name__}")
    print("\nALL DESTINATION STATE TESTS PASSED (5)")
