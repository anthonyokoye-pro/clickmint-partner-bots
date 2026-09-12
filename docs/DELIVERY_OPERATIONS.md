# Delivery operations

## Unified rate limiting and metrics

Reward and Partnership workers use the shared `WORKER_DB_PATH` SQLite database.
Each destination has a persistent token bucket, so a process restart does not
silently remove delivery pacing. Delivery outcomes and latency are recorded per
scope (`reward`, `partnership`) and exposed to authenticated Admin users at:

```text
GET /api/admin/worker/metrics
```

Configuration:

```text
WORKER_DB_PATH       # defaults to STORE_DIR/worker.sqlite3
WORKER_RATE_PER_SECOND=1
WORKER_RATE_BURST=20
```

The burst is intentionally configurable. Use a small burst and a conservative
rate in staging. The worker remains SQLite-based to preserve the zero-budget
constraint; PostgreSQL/Redis/Celery are not required for this boundary.

## Read-only Telegram staging check

The repository includes an opt-in smoke test that calls only official read
operations (`getMe`, `getChat`, `getChatMember`, and
`getChatMemberCount`). It never sends or edits a message and never prints the
bot token.

```bash
STAGING_BOT_TOKEN='...'
STAGING_CHAT_ID='@staging_channel'
export STAGING_BOT_TOKEN STAGING_CHAT_ID
/tmp/cmvenv/bin/python staging_telegram_check.py
```

The staging bot must be an administrator in the destination. Missing variables
produce a safe skip; configured Telegram failures produce a non-zero exit.
Use a dedicated staging bot and destination, never production credentials.
