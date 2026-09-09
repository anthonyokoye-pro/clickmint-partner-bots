"""Centralised destination state machine + structured Telegram error classification.

Before this module, `verified_state` / `status` were written ad hoc from ~15
call sites across two bots, and Telegram exceptions were classified by
substring-matching `str(exc)` in three different places with three different
keyword lists. Both are now in ONE place:

  * `DestinationStateMachine.transition()` is the only sanctioned way to change
    a destination's verification state. Illegal transitions raise, every legal
    one is recorded with a reason, and the *operational* `status` is derived
    from the verification state rather than set independently.

  * `classify_telegram_error()` maps an exception (aiogram or otherwise) to a
    stable `TelegramErrorKind` with a retry policy, so workers and the
    verification service agree on what "permanent" means.

Nothing here talks to Telegram. It is pure and fully testable offline.
"""
from __future__ import annotations

import re

import time
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Verification states
# ---------------------------------------------------------------------------
class VState(str, Enum):
    REGISTERED = "REGISTERED"        # row exists, never verified
    VERIFYING = "VERIFYING"          # a check is in flight
    VERIFIED = "VERIFIED"            # bot is admin with required rights
    DEGRADED = "DEGRADED"            # reachable but rights missing / transient error
    DISCONNECTED = "DISCONNECTED"    # bot left / kicked / owner disconnected bot
    INACCESSIBLE = "INACCESSIBLE"    # Telegram says chat not found / forbidden
    REVOKED = "REVOKED"              # owner's bot token no longer valid
    REMOVED = "REMOVED"              # owner deleted the destination (terminal)


# Legal edges. Anything not listed is a bug at the call site.
_EDGES: dict[VState, set[VState]] = {
    VState.REGISTERED:   {VState.VERIFYING, VState.REMOVED, VState.DISCONNECTED},
    VState.VERIFYING:    {VState.VERIFIED, VState.DEGRADED, VState.DISCONNECTED,
                          VState.INACCESSIBLE, VState.REVOKED, VState.REMOVED},
    VState.VERIFIED:     {VState.VERIFYING, VState.DEGRADED, VState.DISCONNECTED,
                          VState.INACCESSIBLE, VState.REVOKED, VState.REMOVED},
    VState.DEGRADED:     {VState.VERIFYING, VState.VERIFIED, VState.DISCONNECTED,
                          VState.INACCESSIBLE, VState.REVOKED, VState.REMOVED},
    VState.DISCONNECTED: {VState.VERIFYING, VState.REVOKED, VState.REMOVED},
    VState.INACCESSIBLE: {VState.VERIFYING, VState.REVOKED, VState.REMOVED},
    VState.REVOKED:      {VState.VERIFYING, VState.REMOVED},   # after /connectbot again
    VState.REMOVED:      set(),
}

# Operational status is DERIVED, never set by hand.
_STATUS_FOR: dict[VState, str] = {
    VState.REGISTERED: "REGISTERED", VState.VERIFYING: "VERIFYING",
    VState.VERIFIED: "ACTIVE", VState.DEGRADED: "DEGRADED",
    VState.DISCONNECTED: "DISCONNECTED", VState.INACCESSIBLE: "INACCESSIBLE",
    VState.REVOKED: "REVOKED", VState.REMOVED: "REMOVED",
}

# How the background re-verifier should treat each state.
#   None  = never auto-recheck (needs a human action first)
RECHECK_INTERVAL: dict[VState, int | None] = {
    VState.REGISTERED: None,
    VState.VERIFYING: 600,            # stuck in-flight for 10 min → recheck
    VState.VERIFIED: 6 * 3600,        # healthy: every 6 h (well inside the 24 h expiry)
    VState.DEGRADED: 1800,            # recover quickly once rights are restored
    VState.DISCONNECTED: 6 * 3600,    # owner may re-add the bot any time
    VState.INACCESSIBLE: 24 * 3600,   # rarely self-heals; cheap daily probe
    VState.REVOKED: None,             # token gone; only /connectbot fixes it
    VState.REMOVED: None,
}


class IllegalTransition(ValueError):
    pass


@dataclass(frozen=True)
class Transition:
    chat_key: str
    previous: VState
    new: VState
    reason: str
    at: int
    source: str            # "register" | "scan" | "background" | "delivery" | "owner" | "system"

    @property
    def changed(self) -> bool:
        return self.previous != self.new

    def as_dict(self) -> dict:
        return {"chat_key": self.chat_key, "previous": self.previous.value, "new": self.new.value,
                "reason": self.reason, "at": self.at, "source": self.source}


def coerce_state(value) -> VState:
    """Tolerate legacy / free-text values found in old JSON rows."""
    if isinstance(value, VState):
        return value
    text = str(value or "REGISTERED").upper()
    try:
        return VState(text)
    except ValueError:
        # Legacy rows used 'ACTIVE' as a verified_state in a few places.
        return VState.VERIFIED if text == "ACTIVE" else VState.REGISTERED


class DestinationStateMachine:
    """Applies validated transitions to a ChannelRegistry row.

    The registry is the persistence layer; this class owns the *rules*.
    """
    HISTORY_LIMIT = 20

    def __init__(self, registry, *, clock=time.time):
        self.registry = registry
        self.clock = clock

    def state_of(self, chat_key) -> VState:
        row = self.registry.get(chat_key)
        return coerce_state(row.get("verified_state")) if row else VState.REGISTERED

    def can(self, chat_key, new: VState | str) -> bool:
        return coerce_state(new) in _EDGES[self.state_of(chat_key)] or coerce_state(new) == self.state_of(chat_key)

    def transition(self, owner_id, chat_key, new: VState | str, *, reason: str,
                   source: str = "system", **extra_fields) -> Transition:
        """Move a destination to `new`, persisting derived status + history.

        `extra_fields` are passed through to `registry.update` (member counts,
        permissions, etc.) so the caller makes ONE write, atomically with the
        state change. Self-transitions (VERIFIED→VERIFIED on a healthy recheck)
        are allowed and still refresh `extra_fields` + `last_verified_at`.
        """
        new = coerce_state(new)
        previous = self.state_of(chat_key)
        if new != previous and new not in _EDGES[previous]:
            raise IllegalTransition(f"{previous.value} -> {new.value} is not permitted ({reason})")
        if not reason or not reason.strip():
            raise IllegalTransition("a reason is required for every transition")
        now = int(self.clock())
        row = self.registry.get(chat_key) or {}
        history = list(row.get("state_history") or [])[-(self.HISTORY_LIMIT - 1):]
        t = Transition(str(chat_key), previous, new, reason.strip()[:200], now, source)
        if t.changed or not history:
            history.append(t.as_dict())
        fields = {
            "verified_state": new.value,
            "status": _STATUS_FOR[new],
            "bot_added": new == VState.VERIFIED if new != VState.VERIFYING else row.get("bot_added", False),
            "state_history": history,
            "last_transition_at": now,
            **extra_fields,
        }
        if new == VState.VERIFIED:
            fields.setdefault("last_verified_at", now)
            fields["last_error_kind"] = None
        self.registry.update(owner_id, chat_key, **fields)
        return t

    def due_for_recheck(self, rows, *, now: int | None = None) -> list[dict]:
        """Rows whose state's recheck interval has elapsed since the last check."""
        now = int(now if now is not None else self.clock())
        due = []
        for row in rows:
            state = coerce_state(row.get("verified_state"))
            interval = RECHECK_INTERVAL.get(state)
            if interval is None:
                continue
            last = max(int(row.get(k) or 0) for k in ("last_recheck_at", "last_verified_at", "last_transition_at"))
            if now - last >= interval:
                due.append(row)
        return due


# ---------------------------------------------------------------------------
# Telegram error classification
# ---------------------------------------------------------------------------
class TelegramErrorKind(str, Enum):
    TOKEN_REVOKED = "token_revoked"          # 401 Unauthorized
    CHAT_NOT_FOUND = "chat_not_found"        # 400 chat not found / 403 forbidden
    BOT_KICKED = "bot_kicked"                # 403 bot was kicked / blocked by user
    NOT_ENOUGH_RIGHTS = "not_enough_rights"  # 400 need administrator rights
    USER_DEACTIVATED = "user_deactivated"    # 403 user is deactivated
    FLOOD = "flood"                          # 429 retry_after
    MESSAGE_INVALID = "message_invalid"      # bad request about the payload itself
    NETWORK = "network"                      # timeouts, connection reset
    SERVER = "server"                        # 5xx
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    kind: TelegramErrorKind
    permanent: bool                 # True → do not retry; needs a human/owner action
    retry_after: int | None         # seconds, when Telegram told us
    verification_state: VState | None   # what this implies for the destination, if anything
    message: str

    @property
    def retryable(self) -> bool:
        return not self.permanent


_SUBSTRINGS: list[tuple[tuple[str, ...], TelegramErrorKind]] = [
    (("unauthorized", "invalid token", "token is invalid", "not found: bot"), TelegramErrorKind.TOKEN_REVOKED),
    (("bot was kicked", "bot was blocked", "kicked from", "bot is not a member"), TelegramErrorKind.BOT_KICKED),
    (("user is deactivated", "deactivated"), TelegramErrorKind.USER_DEACTIVATED),
    (("chat not found", "channel not found", "group not found", "peer_id_invalid", "chat_id is empty"), TelegramErrorKind.CHAT_NOT_FOUND),
    (("not enough rights", "need administrator rights", "chat_admin_required", "have no rights",
      "chat_write_forbidden", "chat_send_plain_forbidden"), TelegramErrorKind.NOT_ENOUGH_RIGHTS),
    (("too many requests", "retry after", "flood"), TelegramErrorKind.FLOOD),
    (("message is too long", "can't parse entities", "message text is empty", "message to forward not found",
      "message_id_invalid", "wrong file identifier", "message can't be forwarded"), TelegramErrorKind.MESSAGE_INVALID),
    (("timeout", "timed out", "cannot connect", "connection reset", "connection refused",
      "clientconnectorerror", "server disconnected", "temporarily unavailable"), TelegramErrorKind.NETWORK),
    (("bad gateway", "gateway timeout", "internal server error", "service unavailable"), TelegramErrorKind.SERVER),
]

_POLICY: dict[TelegramErrorKind, tuple[bool, VState | None]] = {
    TelegramErrorKind.TOKEN_REVOKED:     (True,  VState.REVOKED),
    TelegramErrorKind.CHAT_NOT_FOUND:    (True,  VState.INACCESSIBLE),
    TelegramErrorKind.BOT_KICKED:        (True,  VState.DISCONNECTED),
    TelegramErrorKind.NOT_ENOUGH_RIGHTS: (True,  VState.DEGRADED),
    TelegramErrorKind.USER_DEACTIVATED:  (True,  None),        # recipient, not destination
    TelegramErrorKind.FLOOD:             (False, None),
    TelegramErrorKind.MESSAGE_INVALID:   (True,  None),        # payload problem, destination fine
    TelegramErrorKind.NETWORK:           (False, None),
    TelegramErrorKind.SERVER:            (False, None),
    TelegramErrorKind.UNKNOWN:           (False, None),
}


def _retry_after(exc) -> int | None:
    value = getattr(exc, "retry_after", None)
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    text = str(exc).lower()
    if "retry after" in text:
        tail = text.split("retry after", 1)[1].strip()
        digits = "".join(ch for ch in tail.split()[0] if ch.isdigit()) if tail else ""
        if digits:
            return int(digits)
    return None


_TOKEN_SHAPE = re.compile(r"\d{4,12}:[A-Za-z0-9_-]{30,}")


def _redact(text: str) -> str:
    return _TOKEN_SHAPE.sub("<redacted-token>", text)


def classify_telegram_error(exc: BaseException) -> ClassifiedError:
    """Stable classification for any exception raised while calling Telegram.

    Prefers aiogram's typed exceptions when present (by class name, so this
    module has no aiogram import), then falls back to message substrings.
    """
    name = type(exc).__name__
    text = str(exc).lower()
    kind = None
    if name in {"TelegramUnauthorizedError"}:
        kind = TelegramErrorKind.TOKEN_REVOKED
    elif name in {"TelegramRetryAfter"}:
        kind = TelegramErrorKind.FLOOD
    elif name in {"TelegramNetworkError", "ClientConnectorError", "TimeoutError", "asyncio.TimeoutError", "ConnectionError"}:
        kind = TelegramErrorKind.NETWORK
    elif name in {"TelegramServerError"}:
        kind = TelegramErrorKind.SERVER
    elif name in {"TelegramForbiddenError"}:
        # Forbidden covers several situations; refine by text below, default to kicked.
        kind = TelegramErrorKind.BOT_KICKED
    if kind is None or kind == TelegramErrorKind.BOT_KICKED:
        for needles, candidate in _SUBSTRINGS:
            if any(n in text for n in needles):
                kind = candidate
                break
    if kind is None:
        kind = TelegramErrorKind.UNKNOWN
    permanent, vstate = _POLICY[kind]
    # aiogram network/HTTP errors can embed the request URL, which contains the
    # bot token. Never let that reach a log, an audit row or a user message.
    return ClassifiedError(kind, permanent, _retry_after(exc), vstate, _redact(str(exc))[:300])


def backoff_seconds(attempt: int, *, base: int = 30, cap: int = 3600, retry_after: int | None = None) -> int:
    """Exponential backoff honouring an explicit Telegram retry_after."""
    if retry_after:
        return min(cap, max(1, retry_after))
    return min(cap, base * (2 ** max(0, min(int(attempt), 8))))


# ---------------------------------------------------------------------------
# Shared "apply a VerificationResult" step used by every bot
# ---------------------------------------------------------------------------
def failed_result_from_reason(row: dict, reason: str | None, *, now: int | None = None):
    """Wrap a non-Telegram failure ('credential missing', 'no chat id') as a
    VerificationResult so it can flow through the same state machine."""
    from telegram_verification import VerificationResult   # local import: avoid cycle
    text = (reason or "verification failed").lower()
    if "no telegram chat id" in text:
        return None                       # nothing to verify against; leave state untouched
    if "credential" in text or "connect your" in text or "token" in text:
        state = VState.REVOKED
    else:
        state = VState.DEGRADED
    return VerificationResult(state.value, False, str(row.get("chat_id") or row.get("username")),
                              checked_at=int(now if now is not None else time.time()),
                              reasons=(reason or "verification failed",))


def apply_verification_result(machine: DestinationStateMachine, row: dict, result, *,
                              source: str, on_change=None) -> tuple[bool, str]:
    """Persist a VerificationResult via validated transitions. Returns (verified, reason).

    `on_change(row, previous_status, new_status, reason)` is called only when the
    operational status actually changed, so callers can audit/notify once.
    """
    owner_id = int(row["owner_id"])
    key = row.get("chat_id") or row.get("username")
    previous_status = row.get("status", "REGISTERED")
    try:
        if result.eligible:
            count = result.member_count
            fields = dict(verification_reasons=[], verification_checks=result.checks or {},
                          permissions=result.permissions or {}, canonical_chat_id=result.chat_id,
                          telegram_chat_type=result.chat_type, last_verified_at=result.checked_at,
                          last_recheck_at=result.checked_at)
            if count is not None:
                fields.update(size=count, telegram_member_count=count, telegram_member_count_source="telegram_api",
                              telegram_member_count_checked_at=result.checked_at)
            t = machine.transition(owner_id, key, VState.VERIFIED, reason="permissions verified",
                                   source=source, **fields)
        else:
            t = machine.transition(owner_id, key, result.state,
                                   reason="; ".join(result.reasons) or "verification failed", source=source,
                                   verification_reasons=list(result.reasons),
                                   verification_checks=result.checks or {},
                                   last_error_kind=getattr(result, "error_kind", None),
                                   last_recheck_at=result.checked_at)
    except IllegalTransition as exc:
        return False, str(exc)
    if t.changed and on_change is not None:
        on_change(row, previous_status, _STATUS_FOR[t.new], t.reason)
    return bool(result.eligible), ("permissions verified" if result.eligible else "; ".join(result.reasons))
