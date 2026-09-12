"""Channel connection and Telegram permission policy primitives.

Telegram API calls remain in bot adapters. This module evaluates returned member
snapshots so the same policy is usable by bots, workers, and the future Mini App.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConnectionState(str, Enum):
    REGISTERED = "REGISTERED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    DEGRADED = "DEGRADED"
    REVOKED = "REVOKED"
    INACCESSIBLE = "INACCESSIBLE"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True)
class TelegramPermissionSnapshot:
    chat_id: str
    chat_type: str
    member_status: str
    can_post_messages: bool = False
    can_edit_messages: bool = False
    can_delete_messages: bool = False
    can_pin_messages: bool = False
    checked_at: int = 0


@dataclass(frozen=True)
class PermissionResult:
    state: ConnectionState
    eligible: bool
    reasons: tuple[str, ...]


def verify_permissions(snapshot: TelegramPermissionSnapshot, *,
                       require_post: bool = True,
                       require_edit: bool = False,
                       require_delete: bool = False,
                       require_pin: bool = False) -> PermissionResult:
    reasons: list[str] = []
    if snapshot.member_status not in {"administrator", "creator"}:
        reasons.append("bot is not an administrator")
    if require_post and snapshot.chat_type == "channel" and not snapshot.can_post_messages:
        reasons.append("bot lacks can_post_messages")
    if require_post and snapshot.chat_type in {"group", "supergroup"} and not snapshot.can_post_messages:
        reasons.append("bot cannot send messages in this chat")
    if require_edit and not snapshot.can_edit_messages:
        reasons.append("bot lacks edit permission")
    if require_delete and not snapshot.can_delete_messages:
        reasons.append("bot lacks delete permission")
    if require_pin and not snapshot.can_pin_messages:
        reasons.append("bot lacks pin permission")
    if reasons:
        state = (ConnectionState.REVOKED if snapshot.member_status in {"left", "kicked"}
                 else ConnectionState.DEGRADED)
        return PermissionResult(state, False, tuple(reasons))
    return PermissionResult(ConnectionState.VERIFIED, True, ())


def task_can_execute(snapshot: TelegramPermissionSnapshot) -> bool:
    return verify_permissions(snapshot, require_post=True).eligible
