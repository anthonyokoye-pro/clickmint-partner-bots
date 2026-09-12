# Admin Mini App Deployment Checklist

## Required environment

```text
REWARD_BOT_TOKEN=<Telegram bot token used for initData validation>
OWNER_USER_ID=<positive Telegram user ID>
ADMIN_ALLOWED_ORIGINS=https://your-admin-domain.example
ADMIN_API_DB_PATH=store/admin_api.sqlite3
BROADCAST_DB_PATH=store/broadcast.sqlite3
CREDIBILITY_DB_PATH=store/credibility.sqlite3
```

Do not use `http://` origins in production. Do not expose bot tokens to the frontend.

## Static frontend

Serve these files from the same trusted HTTPS origin as the API:

```text
/admin_web/index.html
/admin_web/app.js
/admin_web/styles.css
```

The browser must load Telegram's Web App SDK over HTTPS and send the SDK-provided `initData` to the backend. Never trust a Telegram user ID supplied in ordinary JSON.

## Reverse proxy requirements

The reverse proxy must:

- Terminate HTTPS.
- Forward `X-Telegram-Init-Data` unchanged.
- Forward `X-Idempotency-Key` for mutations.
- Preserve `X-Correlation-ID` when supplied.
- Route `/admin_web/` and `/api/admin/` to the same application.
- Reject oversized request bodies before the application.
- Avoid caching authenticated API responses.

## Startup checks

Run the Admin preflight before starting the service. It verifies:

```text
Bot token shape
Owner ID
Frontend artifacts
Backend artifacts
HTTPS origin configuration
```

An origin or credential failure must prevent production startup.

## Integration test checklist

- Valid Telegram `initData` reaches the dashboard.
- Invalid or expired `initData` returns 403.
- Origin rejection is audited.
- Mutations require idempotency keys.
- Replayed mutations do not repeat side effects.
- Scheduled broadcasts do not deliver early.
- Reward and Partnership scopes do not cross-consume deliveries.
- Failed Mint rewards remain retryable.
- Audit export works before any archival policy is considered.
- Backup and restore are verified for Mint, task, broadcast, credibility, and Admin API databases.
- Run `verify_backup_set(manifest.json)` after each backup rehearsal and retain its result with the deployment record.

Financial revenue, deposits, withdrawals, Boost purchases, and external payouts remain disabled.
