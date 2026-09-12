"""Shared task/marketplace eligibility decisions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EligibilityContext:
    account_active: bool
    destination_verified: bool
    destination_status: str
    credibility_score: float
    category_match: bool
    completed_task_before: bool = False
    daily_limit_remaining: int = 1
    boost_radius: int = 0
    required_radius: int = 0
    fraud_block: bool = False
    enforcement_state: str = "ACTIVE"
    safe_mode: bool = False


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]


def can_claim_task(context: EligibilityContext) -> EligibilityResult:
    reasons: list[str] = []
    if not context.account_active:
        reasons.append("account is not active")
    if not context.destination_verified:
        reasons.append("destination is not verified")
    if context.destination_status in {"RESTRICTED", "REMOVED"}:
        reasons.append("destination is restricted")
    if context.credibility_score < 30:
        reasons.append("credibility evidence is insufficient")
    if not context.category_match:
        reasons.append("category is not compatible")
    if context.completed_task_before:
        reasons.append("task was already completed by this user")
    if context.daily_limit_remaining < 1:
        reasons.append("daily task limit reached")
    if context.boost_radius < context.required_radius:
        reasons.append("distribution radius is not available")
    if context.fraud_block:
        reasons.append("account has an unresolved safety restriction")
    if context.enforcement_state in {"RESTRICTED", "SUSPENDED", "BANNED", "REMOVED"}:
        reasons.append(f"entity enforcement state is {context.enforcement_state.lower()}")
    if context.safe_mode:
        reasons.append("CLICKMINT safe mode is active")
    return EligibilityResult(not reasons, tuple(reasons))
