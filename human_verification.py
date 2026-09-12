"""Provider-neutral human-verification and risk challenge foundation.

The core never trusts a browser flag. A production provider adapter must validate
its token server-side and return a short-lived, single-use result here.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from enum import Enum


class VerificationState(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
    SUSPICIOUS = "SUSPICIOUS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class VerificationResult:
    state: VerificationState
    provider: str = "none"
    reference: str = ""
    checked_at: int = 0
    expires_at: int = 0
    reason: str = ""

    @property
    def valid(self) -> bool:
        return self.state == VerificationState.VERIFIED and self.expires_at > int(time.time())


class VerificationReplayError(ValueError):
    pass


class HumanVerificationRegistry:
    """Small in-memory registry for challenge results; persist audit events separately."""
    def __init__(self):
        self._used_tokens: set[str] = set()
        self._results: dict[str, VerificationResult] = {}

    def accept_provider_result(self, *, subject_id: str, token: str, success: bool,
                               provider: str, ttl_seconds: int = 300,
                               reference: str = "", reason: str = "") -> VerificationResult:
        if not token:
            return VerificationResult(VerificationState.FAILED, provider=provider,
                                      reason="missing token", checked_at=int(time.time()))
        if token in self._used_tokens:
            raise VerificationReplayError("verification token already used")
        self._used_tokens.add(token)
        now = int(time.time())
        state = VerificationState.VERIFIED if success else VerificationState.FAILED
        result = VerificationResult(state, provider=provider, reference=reference,
                                    checked_at=now,
                                    expires_at=now + max(1, int(ttl_seconds)) if success else now,
                                    reason=reason)
        self._results[str(subject_id)] = result
        return result

    def get(self, subject_id: str) -> VerificationResult:
        result = self._results.get(str(subject_id))
        if not result:
            return VerificationResult(VerificationState.NOT_CHECKED)
        if result.state == VerificationState.VERIFIED and result.expires_at <= int(time.time()):
            return VerificationResult(VerificationState.EXPIRED, provider=result.provider,
                                      reference=result.reference, checked_at=result.checked_at,
                                      expires_at=result.expires_at, reason="verification expired")
        return result


def risk_decision(*, failed_actions: int = 0, referral_velocity: int = 0,
                  enforcement_state: str = "ACTIVE", verification: VerificationState = VerificationState.NOT_CHECKED) -> str:
    """Return a conservative challenge recommendation, never a guilt verdict."""
    if enforcement_state in {"BANNED", "REMOVED"}:
        return "BLOCK"
    if enforcement_state in {"SUSPENDED", "RESTRICTED"}:
        return "REVIEW"
    if verification in {VerificationState.FAILED, VerificationState.SUSPICIOUS}:
        return "CHALLENGE"
    if failed_actions >= 5 or referral_velocity >= 20:
        return "CHALLENGE"
    return "ALLOW"
