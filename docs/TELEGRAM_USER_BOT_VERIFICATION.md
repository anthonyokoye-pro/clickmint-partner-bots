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

## Security operations

Set `CLICKMINT_CREDENTIAL_KEY` to a long random secret in the deployment secret
manager. Never commit it or print it. Replacing the key intentionally makes
stored credentials unreadable. `/disconnectbot` removes the encrypted record.
Credentials use authenticated AES-GCM encryption with associated owner data.
A managed KMS should still protect `CLICKMINT_CREDENTIAL_KEY` in production.
Legacy HMAC-stream records remain read-compatible so operators can rotate them
by reconnecting the bot.

## Supported checks

The verification service performs real Telegram API calls for:

- destination resolution with `getChat`
- bot membership and administrator status with `getChatMember`
- chat-type-aware permissions
- member count with `getChatMemberCount`

Both Reward and Partnership bots expose `/scan`, `/stats`, `/disconnectbot`, and
`📊 Check Stats` where available. A statistics failure never fabricates a number. Failure messages identify the
problem and tell the owner to add/promote the bot, grant the missing permission,
or retry when Telegram is temporarily unavailable.
