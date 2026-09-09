# User-owned Telegram bot verification

ClickMint requires each destination owner to connect their own Telegram bot. The
shared ClickMint bot cannot truthfully verify or publish using an arbitrary
user-owned bot identity.

## Setup

1. Create a bot with BotFather.
2. In the ClickMint bot, send `/connectbot TOKEN`.
3. ClickMint validates the token with `getMe`, deletes the token message where
   Telegram permits it, and stores an encrypted credential record.
4. Add that bot to the channel or group as an administrator.
5. Grant the permissions required for the chat type, including posting in a
   channel.
6. Send `/register @destination`.

Subscriber/member counts are never entered manually. Registration and the
`📊 Check Stats` action retrieve the current value from Telegram's
`getChatMemberCount` endpoint.

## Verification states

`REGISTERED`, `VERIFYING`, `VERIFIED`, `DEGRADED`, `REVOKED`, `INACCESSIBLE`,
and `DISCONNECTED` are persisted on the managed destination. Participation is
fail-closed and requires a destination to be `VERIFIED`, active, eligible, and
currently permitted.

## State machine (destination_state.py)

Every destination transition in both bots goes through `DestinationStateMachine`.
Nothing writes `verified_state=` directly any more.

- States: `REGISTERED → VERIFYING → VERIFIED | DEGRADED | INACCESSIBLE | DISCONNECTED | REVOKED`, plus terminal `REMOVED`.
- `status` (ACTIVE/DEGRADED/…) is *derived* from `verified_state`; `bot_added` is true only for VERIFIED.
- Illegal edges raise `IllegalTransition` (e.g. REVOKED → VERIFIED without re-verifying, anything out of REMOVED).
- Every transition records `reason` and `source` (`owner`, `scan`, `delivery`, `background`, `telegram`, `reconcile`) in `state_history` (last 20 kept) and `last_transition_at`.
- A recheck that confirms the current state is `changed=False`: fields are refreshed, no history entry, no owner notification.

### Re-verification schedule

| State | Recheck interval | Trigger |
|---|---|---|
| VERIFIED | 6 h | background |
| DEGRADED | 30 min | background |
| DISCONNECTED | 6 h | background |
| INACCESSIBLE | 24 h | background |
| VERIFYING | 10 min (stuck-state recovery) | background |
| REGISTERED / REVOKED / REMOVED | never | owner action only |

The worker (`_background_reverify` in each bot) runs on the 15 s notify tick but only
probes destinations that are due, oldest first, at most `REVERIFY_BATCH` (default 5)
per tick. It pauses in safe mode. Owners are messaged only when the operational
status actually changed. This replaced a loop that re-verified every destination of
every member on every tick.

Owners can force a check at any time from `/mychannels` → **🔄 Re-verify**
(and see check breakdown/history via ℹ️, or remove via 🗑). The re-verify tap
passes through the enforcement gate like registration.

### Telegram error classification

`classify_telegram_error` maps aiogram/HTTP failures to `TelegramErrorKind` and a
target state so failures are actionable instead of "DEGRADED with an error string":

| Kind | Example | Permanent | New state |
|---|---|---|---|
| token_revoked | 401 Unauthorized | yes | REVOKED |
| chat_not_found | 400 chat not found | yes | INACCESSIBLE |
| bot_kicked | 403 bot was kicked | yes | DISCONNECTED |
| not_enough_rights | 400 need administrator rights | yes | DEGRADED |
| flood | 429, `retry_after` | no | unchanged (backoff) |
| network / server / unknown | timeouts, 5xx | no | unchanged (backoff) |
| message_invalid / user_deactivated | payload / recipient problems | yes | unchanged |

`backoff_seconds(kind, attempt, retry_after)` gives 30 s → 1 h exponential backoff and
honours Telegram's `retry_after` for floods. `last_error_kind` is stored on the row.

## Security operations

Set `CLICKMINT_CREDENTIAL_KEY` to a long random secret in the deployment secret
manager. Never commit it or print it. Replacing the key intentionally makes
stored credentials unreadable. `/disconnectbot` removes the encrypted record.
Credentials use authenticated AES-GCM encryption with associated owner data.
A managed KMS should still protect `CLICKMINT_CREDENTIAL_KEY` in production.
Legacy HMAC-stream records remain read-compatible so operators can rotate them
by reconnecting the bot.

## Supported checks

Partnership offer notifications and Reward Bot direct task publication use the
verified destination owner's bot session. The shared ClickMint bot is not used
as proof of, or a substitute for, the owner's administrator permissions.

The verification service performs real Telegram API calls for:

- destination resolution with `getChat`
- bot membership and administrator status with `getChatMember`
- chat-type-aware permissions
- member count with `getChatMemberCount`

Both Reward and Partnership bots expose `/scan`, `/stats`, `/disconnectbot`, and
`📊 Check Stats` where available. A statistics failure never fabricates a number. Failure messages identify the
problem and tell the owner to add/promote the bot, grant the missing permission,
or retry when Telegram is temporarily unavailable.
