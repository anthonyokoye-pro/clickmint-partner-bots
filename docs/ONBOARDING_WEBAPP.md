# Secure bot-token onboarding (Mini App)

**Modules:** `onboarding_api.py`, `onboarding_web/`, routes in `admin_http.py`, wiring in `admin_server.py`.
**Tests:** `test_onboarding.py`.

## Why not `/connectbot <token>`

A token typed into a Telegram chat has already crossed Telegram's servers, sits in the chat
history of every device the member has open, and the bot's `deleteMessage` is best-effort
(fails after 48 h, in some clients, or without rights). That is the wrong channel for a secret.

## Flow

1. Member sends `/connectbot` (no argument). If `ONBOARDING_WEBAPP_URL` is set, the bot answers
   with a **🔐 Connect bot securely** `web_app` button. Without the URL the legacy in-chat flow
   remains (and a member who still pastes a token in chat is told to rotate it).
2. The page (`/onboarding_web/`) takes the token in a password field and POSTs it to
   `/api/onboarding/connect` with `X-Telegram-Init-Data`.
3. The server validates `initData` with the **reward bot's** token (HMAC, ≤10 min old).
   The user id comes only from that signature — nothing in the body is trusted for identity.
4. The enforcement gate is consulted (restricted/banned users cannot connect).
5. `getMe` is called with the submitted token; on success the token is stored **encrypted
   (AES-GCM)** in the shared verification DB under the initData user id. One bot per ClickMint
   account is enforced across Reward and Partnership because the store is shared.
6. If the member changed bots, all their destinations transition to `DISCONNECTED`
   (source `owner`) and must be re-verified.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/onboarding/status` | signed initData | own connection + destinations |
| POST | `/api/onboarding/connect` | signed initData, JSON `{token}` | validate + store token |
| POST | `/api/onboarding/disconnect` | signed initData | remove token, disconnect destinations |
| GET | `/onboarding_web/*` | — | static page, `Cache-Control: no-store` |

These routes exist only when `AdminWSGI` is built with an `onboarding` object; they are **not**
admin-scoped and never touch the admin authorisation path.

## What never happens

- The token is never echoed in any response, never written to an audit row or log
  (`onboarding_api.redact` strips token-shaped strings from every error), never sent as a
  Telegram message, and the page clears the input as soon as the request returns.
- The onboarding POST body is deliberately **not** run through the admin idempotency cache —
  caching a body that contains a secret would persist it.

## Deployment

```
ONBOARDING_WEBAPP_URL=https://<your-admin-host>/onboarding_web/
ADMIN_ALLOWED_ORIGINS=https://<your-admin-host>
```
The page is served by the same HTTPS process as the Admin Mini App; Telegram only opens
`web_app` buttons on HTTPS origins.
