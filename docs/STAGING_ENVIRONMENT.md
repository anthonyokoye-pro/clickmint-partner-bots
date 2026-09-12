# ClickMint staging environment

Staging must use separate Telegram and storage resources:

- Dedicated Reward, Partnership, and Admin bots created specifically for staging.
- Dedicated staging channel and group/supergroup.
- Dedicated HTTPS Admin Web App origin.
- Dedicated SQLite directory and database files.
- Dedicated encryption key.
- Financial flags disabled.

## Configure

```bash
cp .env.staging.example .env.staging
# Edit .env.staging with the dedicated staging values.
python3 staging_preflight.py --env-file .env.staging
```

The preflight rejects placeholder or duplicate bot tokens, non-HTTPS origins,
paths outside the staging directory, reused/insecure encryption keys, and any
enabled financial flag. It never prints secrets and does not contact Telegram.

## Start

```bash
./run_staging.sh .env.staging
```

`run_staging.sh` validates the file, loads it only into the child process
environment, and starts the normal bot supervisor. Never run it with a
production `.env` file.

## Telegram verification

After the staging bots are running, use a dedicated staging bot/channel for the
read-only API check:

```bash
STAGING_BOT_TOKEN='staging token' \
STAGING_CHAT_ID='@staging_channel' \
python3 staging_telegram_check.py
```

The check uses `getMe`, `getChat`, `getChatMember`, and
`getChatMemberCount` only. It does not send or modify Telegram content.
