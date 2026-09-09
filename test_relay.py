"""Relay resolver: which bot may forward which copy of a post (offline)."""
from __future__ import annotations

import relay
from relay import Source, Destination, resolve


def _src(**kw):
    base = dict(inbox_chat_id=1001, inbox_message_id=55)
    base.update(kw)
    return Source(**base)


def test_platform_route_when_platform_bot_is_admin():
    plan = resolve(_src(), Destination(chat_id="@bob", owner_id=1002, platform_bot_admin=True))
    assert plan.ok and plan.route == "platform" and plan.forwarder == "platform"
    assert (plan.from_chat_id, plan.message_id) == (1001, 55)
    print("PASS platform bot forwards its own inbox copy when it administers the destination")


def test_owner_bot_never_forwards_from_platform_inbox():
    """The production bug: owner's bot asked to forward from a private chat it cannot read."""
    plan = resolve(_src(), Destination(chat_id="@bob", owner_id=1002, platform_bot_admin=False))
    assert not plan.ok and plan.route == "unavailable"
    assert "inbox" in plan.reason
    print("PASS owner bot is never handed the platform inbox copy")


def test_owner_origin_route_only_for_owner_administered_channel():
    src = _src(origin_chat_id=-100777, origin_message_id=9, origin_kind="channel")
    own = Destination(chat_id="@bob", owner_id=1002, owner_admin_chat_ids=frozenset({"-100777"}))
    plan = resolve(src, own)
    assert plan.ok and plan.route == "owner_origin" and plan.forwarder == "owner"
    assert (plan.from_chat_id, plan.message_id) == (-100777, 9)
    other = Destination(chat_id="@bob", owner_id=1002, owner_admin_chat_ids=frozenset({"-100999"}))
    plan = resolve(src, other)
    assert not plan.ok and "origin channel" in plan.reason
    # A user-origin forward has no channel to read from.
    plan = resolve(_src(origin_kind="user"), own)
    assert not plan.ok
    print("PASS owner bot may forward from an origin channel only if it administers that channel")


def test_platform_may_try_never_licenses_owner_bot():
    dest = Destination(chat_id="@bob", owner_id=1002, platform_bot_admin=False)
    plan = resolve(_src(), dest, platform_may_try=True)
    assert plan.route == "platform" and plan.forwarder == "platform" and "unproven" in plan.reason
    assert not resolve(Source(), dest, platform_may_try=True).ok
    print("PASS platform_may_try attempts the platform route only, never the owner bot")


def test_platform_route_preferred_over_owner_origin():
    src = _src(origin_chat_id=-100777, origin_message_id=9, origin_kind="channel")
    dest = Destination(chat_id="@bob", owner_id=1002, platform_bot_admin=True,
                       owner_admin_chat_ids=frozenset({"-100777"}))
    assert resolve(src, dest).route == "platform"
    print("PASS platform route is preferred when both are legal")


def test_no_source_fails_closed():
    plan = resolve(Source(), Destination(chat_id="@bob", owner_id=1002, platform_bot_admin=True))
    assert not plan.ok and "no forwardable" in plan.reason
    print("PASS no source → unavailable, never a fabricated post")


def test_source_round_trips_through_persisted_shapes():
    class Chat:  id = -100555
    class Origin:
        type = "channel"; chat = Chat(); message_id = 42
    class Msg:
        chat = type("C", (), {"id": 1001})(); message_id = 7; forward_origin = Origin()
    src = Source.from_message(Msg())
    assert src.has_inbox and src.has_channel_origin
    rec = src.as_record()
    assert Source.from_record(rec) == src
    # legacy shapes
    assert Source.from_record({"from_chat_id": 1, "from_message_id": 2}).has_inbox
    assert Source.from_record({"source_chat_id": 1, "source_message_id": 2}).has_inbox
    assert not Source.from_record({"source_chat_id": 1, "source_message_id": 2}).has_channel_origin
    print("PASS Source round-trips through message, pending, task, schedule and audit shapes")


def test_destination_from_registry():
    row = {"chat_id": "@bob", "owner_id": 1002, "verified_state": "VERIFIED", "telegram_bot_id": 111111,
           "canonical_chat_id": -100888}
    others = [row, {"chat_id": "@bobnews", "owner_id": 1002, "verified_state": "VERIFIED", "canonical_chat_id": -100777},
              {"chat_id": "@old", "owner_id": 1002, "verified_state": "REVOKED", "canonical_chat_id": -100666}]
    d = relay.destination_from_registry(row, platform_bot_id=111111, owner_rows=others)
    assert d.platform_bot_admin, "verified with the platform bot's own id ⇒ platform admin"
    assert "-100777" in d.owner_admin_chat_ids and "-100666" not in d.owner_admin_chat_ids
    d2 = relay.destination_from_registry(row, platform_bot_id=999, owner_rows=others)
    assert not d2.platform_bot_admin
    d3 = relay.destination_from_registry({**row, "verified_state": "DEGRADED"}, platform_bot_id=111111, owner_rows=others)
    assert not d3.platform_bot_admin, "a degraded destination is not a platform-admin route"
    print("PASS registry rows map to Destination with fail-closed admin flags")


TESTS = [test_platform_route_when_platform_bot_is_admin, test_owner_bot_never_forwards_from_platform_inbox,
         test_owner_origin_route_only_for_owner_administered_channel, test_platform_route_preferred_over_owner_origin, test_platform_may_try_never_licenses_owner_bot,
         test_no_source_fails_closed, test_source_round_trips_through_persisted_shapes, test_destination_from_registry]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print(f"\nALL RELAY TESTS PASSED ({len(TESTS)})")
