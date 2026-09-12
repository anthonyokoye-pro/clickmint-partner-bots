"""Shared application-service boundary for enforcement checks.

The 2026-09 audit found enforcement was only consulted inside UI handlers. This
module gives every runtime (reward bot, partnership bot, admin bot, scheduler,
Admin API) ONE object to ask "may this actor do this thing right now?" so the
decision cannot drift between entry points.

Design rules (see docs/COMPLIANCE_AND_ENFORCEMENT.md):
  * fail closed — if the store is unreachable the answer is "blocked";
  * never infer guilt — this gate only READS states a human already recorded;
  * the owner is never blocked by per-entity state, but IS blocked by safe mode
    for automated actions (safe mode exists precisely to stop the machine).
"""
from __future__ import annotations

from dataclasses import dataclass

from enforcement import CAPABILITIES, EnforcementError, EnforcementStore


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    state: str = "ACTIVE"
    safe_mode: bool = False

    def __bool__(self) -> bool:
        return self.allowed


_MESSAGES = {
    "FLAGGED": "",  # flagged entities keep participating; a human is reviewing
    "RESTRICTED": "this account is restricted pending review",
    "SUSPENDED": "this account is temporarily suspended",
    "BANNED": "this account has been removed from CLICKMINT",
    "REMOVED": "this account has been removed from CLICKMINT",
}


class EnforcementGate:
    def __init__(self, store: EnforcementStore | None, *, owner_user_id=None):
        self.store = store
        self.owner_user_id = str(owner_user_id) if owner_user_id else None

    # -- queries -----------------------------------------------------------
    def safe_mode(self) -> bool:
        if self.store is None:
            return False
        try:
            return self.store.emergency_enabled("safe_mode")
        except Exception:
            return True  # fail closed

    def state(self, entity_id, entity_type: str) -> str:
        if self.store is None:
            return "ACTIVE"
        try:
            return self.store.get(entity_id, entity_type)["state"]
        except Exception:
            return "SUSPENDED"  # fail closed

    def check(self, entity_id, entity_type: str, capability: str, *,
              is_owner: bool = False) -> GateDecision:
        if capability not in CAPABILITIES:
            raise EnforcementError("unknown capability")
        if self.store is None:
            return GateDecision(True)
        safe = self.safe_mode()
        if safe and capability != "registration":
            return GateDecision(False, "CLICKMINT is in emergency safe mode; automated actions are paused", "ACTIVE", True)
        owner = is_owner or (self.owner_user_id is not None and str(entity_id) == self.owner_user_id)
        if owner:
            return GateDecision(True, "", "ACTIVE", safe)
        if safe:  # registration during safe mode is also paused for non-owners
            return GateDecision(False, "CLICKMINT is in emergency safe mode; registration is paused", "ACTIVE", True)
        current = self.state(entity_id, entity_type)
        try:
            ok = self.store.allowed(entity_id, entity_type, capability)
        except Exception:
            ok = False
        if ok:
            return GateDecision(True, "", current, False)
        return GateDecision(False, _MESSAGES.get(current) or "this account is not permitted to do that", current, False)

    def check_many(self, subjects, capability: str, *, is_owner: bool = False) -> GateDecision:
        """First blocking decision among (entity_id, entity_type) pairs wins.

        Used where a user acts *through* a destination: both the user and the
        channel must be clear."""
        for entity_id, entity_type in subjects:
            decision = self.check(entity_id, entity_type, capability, is_owner=is_owner)
            if not decision:
                return decision
        return GateDecision(True, "", "ACTIVE", self.safe_mode())

    # -- convenience for bot handlers ---------------------------------------
    def block_message(self, decision: GateDecision) -> str:
        if decision.safe_mode:
            return "⏸ " + decision.reason + ". Please try again later."
        return ("⛔ " + decision.reason + ". If you believe this is a mistake you can "
                "file an appeal with /appeal <your explanation>; a human reviews every appeal.")
