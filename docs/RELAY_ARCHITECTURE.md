# Source-message relay architecture

**Decision date:** 2026-09-09 · **Module:** `relay.py` · **Tests:** `test_relay.py`, `test_bots.py::test_direct_delivery_uses_a_bot_that_can_read_the_source`

## The problem this fixes

Telegram's `forwardMessage` only succeeds when the **forwarding bot can read the source message**.

Before this decision every reward-bot delivery path stored the member's *private chat with the
platform bot* (`from_chat_id = msg.chat.id`) as the source, then asked the **destination owner's**
bot to forward from it (`direct_deliver`, `_execute_task_payload`). One bot cannot read another
bot's private chats, so every owner-bot delivery would fail in production with
`Bad Request: message to forward not found`. The offline mock returned success for any
`ForwardMessage`, so nothing detected it.

## Options considered

| Option | Verdict |
|---|---|
| Owner bot `copyMessage`s the inbox copy | Same read restriction — still fails. |
| Platform bot downloads media and the owner bot re-uploads a rebuilt post | Loses Telegram's attribution header and inline keyboard; the compliance model requires the original forward. Rejected. |
| MTProto user session to read anything | Account-level automation — out of scope by project rule. Rejected. |
| **Route per destination to the one bot that can read a legal source** | Chosen. |

## The rule (`relay.resolve`)

For each (source, destination) pair, in order:

1. **`platform`** — the platform bot forwards the copy from *its own inbox*
   (`inbox_chat_id/inbox_message_id`). Legal only when the platform bot is an administrator of
   the destination (destination verified with `telegram_bot_id == platform bot id`, or
   `platform_bot_admin` set).
2. **`owner_origin`** — the destination owner's bot forwards from the **origin channel**
   (`forward_origin.chat.id/message_id`). A bot can read a channel only as an admin, so this is
   legal only when the origin is one of the *same owner's* VERIFIED destinations.
   *Chain-agree and legacy `/schedule` pass `platform_may_try=True`:* the platform bot posted
   the offer inside that destination itself, so it attempts route 1 and Telegram's answer is
   final (classified via `destination_state` on failure). This flag never lets an owner bot
   touch the inbox copy.
3. **`unavailable`** — fail closed: nothing is posted, an audit row with `relay="unavailable"`
   and the reason is written, and the member gets an honest message.

`copyMessage` and rebuilt text stand-ins are never used.

## Data captured at submission

`relay.Source.from_message(msg)` records both candidates on the pending snapshot, task payload,
schedule record, available-queue item and the `offered` audit row:

```
inbox_chat_id, inbox_message_id       # the member's copy in the platform bot's chat
origin_chat_id, origin_message_id     # only when forward_origin.type == "channel"
origin_kind                           # channel | user | hidden_user | chat | None
```

Legacy records with only `from_chat_id`/`source_chat_id` still load (`Source.from_record`) and
resolve to route 1 or `unavailable`.

## Paths now routed through `_relay_forward`

- `direct_deliver` (member submission → direct-mode destination)
- `_execute_task_payload` (marketplace task claim)
- `chain:agree` (partner agrees to a chain offer)
- scheduler `run_due`

The partnership bot performs **no bot-side forward at all**: "Agree" tells the partner to post
manually. That is a deliberate human-in-the-loop design, not a relay gap.

## Test guard

`test_bots.MockSession.visible_chats` enforces the read rule: a `ForwardMessage` whose
`from_chat_id` the forwarding bot cannot read raises `TelegramBadRequest` like production. The
regression test runs all three routes and was verified to fail against the previous code.

## Operational consequence

For a member's post to reach another owner's destination automatically, **either** the platform
bot must be an admin there (owner adds it), **or** the post must originate from a channel the
receiving owner's bot administers. Otherwise the offer remains a chain offer the owner can
still accept and forward by hand.
