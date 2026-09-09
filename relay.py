"""Source-message relay: decide WHICH bot may forward WHICH copy of a post.

Why this exists
---------------
Telegram's forwardMessage only works when the *forwarding bot* can read the
source message. Before this module, delivery code stored the member's private
chat with the platform bot as the source, then asked the *destination owner's*
bot to forward from it. A bot cannot read another bot's private chats, so every
owner-bot delivery would fail in production with "message to forward not
found" — invisibly, because the offline mock accepted any ForwardMessage.

Decision (2026-09-09)
---------------------
* Attribution-preserving `forwardMessage` only. Never `copyMessage`, never a
  rebuilt text stand-in: the compliance model requires the original header.
* Two legal relay routes, tried in order, fail closed otherwise:

  1. ``platform``  — the platform bot forwards the copy from its own inbox
     (``source.inbox_chat_id/inbox_message_id``). Requires the platform bot to
     be an administrator of the destination (``destination.platform_bot_admin``).
  2. ``owner_origin`` — the destination owner's bot forwards from the ORIGIN
     channel (``source.origin_chat_id/origin_message_id``), which a bot can
     only read if it administers that channel. So this route is legal only when
     the origin is one of the *same owner's* verified destinations.

  Anything else is ``unavailable``: no post, an audit row, an honest message.

Nothing here talks to Telegram. It resolves a plan; the bots execute it with the
bot object the plan names, so the decision is testable offline and the mock
session can be strict about who forwards what.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    """Where a post can be forwarded FROM."""
    inbox_chat_id: int | str | None = None      # member's private chat with the platform bot
    inbox_message_id: int | None = None
    origin_chat_id: int | str | None = None     # forward_origin.chat.id (channel) if any
    origin_message_id: int | None = None
    origin_kind: str | None = None              # "channel" | "user" | "hidden_user" | "chat" | None

    @classmethod
    def from_message(cls, msg) -> "Source":
        origin = getattr(msg, "forward_origin", None)
        o_chat = o_msg = None
        o_kind = getattr(origin, "type", None) if origin is not None else None
        if o_kind == "channel":
            chat = getattr(origin, "chat", None)
            o_chat = getattr(chat, "id", None)
            o_msg = getattr(origin, "message_id", None)
        return cls(inbox_chat_id=getattr(getattr(msg, "chat", None), "id", None),
                   inbox_message_id=getattr(msg, "message_id", None),
                   origin_chat_id=o_chat, origin_message_id=o_msg, origin_kind=o_kind)

    @classmethod
    def from_record(cls, rec: dict) -> "Source":
        """Rebuild from any of the persisted shapes (pending / task payload / schedule / audit)."""
        return cls(
            inbox_chat_id=rec.get("inbox_chat_id", rec.get("from_chat_id", rec.get("source_chat_id"))),
            inbox_message_id=rec.get("inbox_message_id", rec.get("from_message_id", rec.get("source_message_id"))),
            origin_chat_id=rec.get("origin_chat_id"),
            origin_message_id=rec.get("origin_message_id"),
            origin_kind=rec.get("origin_kind"),
        )

    def as_record(self) -> dict:
        return {"inbox_chat_id": self.inbox_chat_id, "inbox_message_id": self.inbox_message_id,
                "origin_chat_id": self.origin_chat_id, "origin_message_id": self.origin_message_id,
                "origin_kind": self.origin_kind}

    @property
    def has_inbox(self) -> bool:
        return self.inbox_chat_id is not None and self.inbox_message_id is not None

    @property
    def has_channel_origin(self) -> bool:
        return self.origin_kind == "channel" and self.origin_chat_id is not None and self.origin_message_id is not None


@dataclass(frozen=True)
class Destination:
    chat_id: int | str
    owner_id: int | None
    platform_bot_admin: bool = False
    owner_admin_chat_ids: frozenset = field(default_factory=frozenset)  # chats the owner's bot administers


@dataclass(frozen=True)
class Plan:
    route: str                      # "platform" | "owner_origin" | "unavailable"
    forwarder: str                  # "platform" | "owner" | ""
    from_chat_id: int | str | None
    message_id: int | None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.route != "unavailable"


def _same(a, b) -> bool:
    return str(a) == str(b)


def resolve(source: Source, destination: Destination, *, platform_may_try: bool = False) -> Plan:
    """Pick the one legal relay route, or fail closed.

    `platform_may_try` — the caller is already acting AS the platform bot inside
    the destination (e.g. a chain offer the platform bot itself posted there, and
    a member tapped Agree on it). We have no stronger proof than that of admin
    rights, so the platform route is attempted and Telegram's answer is the
    truth (classified by destination_state on failure). It is never a licence
    for an OWNER bot to touch the inbox copy.
    """
    if source.has_inbox and (destination.platform_bot_admin or platform_may_try):
        return Plan("platform", "platform", source.inbox_chat_id, source.inbox_message_id,
                    "platform bot forwards its inbox copy"
                    + ("" if destination.platform_bot_admin else " (rights unproven; Telegram decides)"))
    if source.has_channel_origin and destination.owner_id is not None:
        if any(_same(source.origin_chat_id, cid) for cid in destination.owner_admin_chat_ids):
            return Plan("owner_origin", "owner", source.origin_chat_id, source.origin_message_id,
                        "owner's bot administers the origin channel")
        why = "owner's bot cannot read the origin channel (not one of the owner's verified destinations)"
    elif source.has_inbox:
        why = ("destination owner's bot cannot read the member's inbox copy; "
               "platform bot is not an admin of the destination")
    else:
        why = "no forwardable source message"
    return Plan("unavailable", "", None, None, why)


def destination_from_registry(row: dict, *, platform_bot_id: int | None, owner_rows: list[dict]) -> Destination:
    """Build a Destination from a channel_registry row plus the owner's other rows."""
    admin_ids = set()
    for r in owner_rows:
        if r.get("verified_state") == "VERIFIED":
            for key in ("canonical_chat_id", "chat_id", "username"):
                if r.get(key) is not None:
                    admin_ids.add(str(r[key]))
    platform_admin = bool(row.get("platform_bot_admin")) or (
        platform_bot_id is not None and row.get("telegram_bot_id") is not None
        and int(row["telegram_bot_id"]) == int(platform_bot_id) and row.get("verified_state") == "VERIFIED")
    return Destination(chat_id=row.get("canonical_chat_id") or row.get("chat_id") or row.get("username"),
                       owner_id=int(row["owner_id"]) if row.get("owner_id") is not None else None,
                       platform_bot_admin=platform_admin,
                       owner_admin_chat_ids=frozenset(admin_ids))
