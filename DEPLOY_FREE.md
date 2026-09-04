# CLICKMINT — run everything for $0/month

## The key misconception (read this first)
> "I can't afford to host all these bots." — **You don't pay per bot.**
> One single free server runs the reward bot, the partnership bot and the admin
> panel **all at the same time.** Each bot is just one Python process on the
> same machine. Two bots on one tiny 1GB box is trivial.

And the "database" you're worried about is also **free**: the bot already uses a
single JSON file (`store.py`), and the honest free upgrade is **SQLite** — also a
file, hosted on the same free box. You don't need a managed Postgres server until
you have thousands of concurrent users (see the DB section).

So the real question isn't "can I afford it" — it's **"where do I get one free
always-on machine."**

---

## The one requirement that decides your host
These bots use **long-polling** — they run a *continuous* process that asks Telegram
for new messages. That means the host **must keep a long-running process alive.**

❌ **Avoid these for a polling bot:**
- **Render free tier** — *sleeps after 15 minutes of no HTTP requests.* A polling bot
  makes no inbound requests → it sleeps, and your bot goes dark. [1](https://kuberns.com/blogs/deploy-telegram-bot/)
- **Vercel / AWS Lambda / Cloud Functions** — cannot run persistent polling at all.
- **Heroku / Railway free** — these no longer offer usable free tiers for this.

✅ **Shortlist (truly free, keep a process alive):**

| Host | Free offer | Card needed? | Best for | Caveat |
|---|---|---|---|---|
| **Oracle Cloud Always Free** | Ampere A1 VPS: up to **4 OCPU / 24GB RAM / 100GB** free forever | Yes (verification, no charge) | ⭐ **Best** — real VPS, run both bots under systemd | Signup + a 20% idle-reclaim risk; ARM CPU (fine for Python) [2](https://veristamp.in/blog/self-hosted-ai-agent-oracle-always-free-picoclaw-tailscale/) [4](https://webmatrices.com/app-ideas/discord-bot-host-advisor/compare?a=oracle-cloud-always-free) |
| **Your own device** (old laptop / **Raspberry Pi**) | Free, forever, no signup | No | Easiest + private | Needs constant power + internet |
| **Serv00** | Free shell, 3GB SSD/512MB RAM, run with `screen`/`tmux` | No | Lightweight, no card [3](https://github.com/Harshit-shrivastav/Free-Telegram-bot-hosting) | Smaller box |
| **Alwaysdata** | 100MB, includes MySQL/Postgres/Python, SSH | No | App + DB in one place | Tiny |

**My honest recommendation for you:** start on **an old laptop or a Raspberry Pi at
home** (absolutely free, no account, you control it). When you're ready to leave it
running 24/7 and want it off your power/internet, move it to **Oracle Cloud Always
Free**. Both run the same code.

---

## Minimal, honest hosting plan
1. **One free machine** (Pi / old laptop / Oracle A1).
2. **Install Python 3.10+** + `pip install -r requirements.txt` — note `aiogram`.
3. **Run all three bots** as background services with `systemd` (`clickmint.service`,
   `clickmint-partner.service`, `clickmint-admin.service`). Each bot = one process on the
   SAME machine → **no per-bot cost.**
4. **Keep the JSON store** (it's fine for hundreds of users) — or switch to SQLite
   (also free, also on the same machine).

---

## Database — you don't need to pay for it

| Now (hundreds of users) | Later (thousands + concurrency) |
|---|---|
| **SQLite** — a single file, free, zero setup, runs on the same free box. | Move to **Postgres** (e.g. Supabase) |
| This is the correct choice for a single-server bot. | Only when you hit write-locking/"database is locked" errors or a 2nd server. |

- **SQLite is the right free answer**: near-zero RAM, no daemon, backup = copy one file,
  and it's more than enough for a solo app under ~10k daily users. [2](https://www.kunalganglani.com/blog/sqlite-vs-postgresql-for-apps) [4](https://botmonster.com/self-hosting/self-hosted-databases-postgres-sqlite-mariadb/)
- **Supabase free tier**: exists (500MB, 2 projects) but **pauses after ~1 week of
  inactivity** (bad for a bot) and **jumps to ~$25/mo** past its limits. [1](https://www.mindstudio.ai/blog/supabase-vs-planetscale) [3](https://www.reddit.com/r/Supabase/comments/1qgny6k/love_supabase_but_the_25mo_pricing_tier_killed_my/)
  → Don't start there. Use SQLite now; move later only on real demand.

**Honest rule:** don't spend money on a DB until you have a reason to. Build the
network first, earn users, and *then* buy the upgrade when it pays for itself.

---

## Step 1 — create the three bots in @BotFather
Open [@BotFather](https://t.me/BotFather) in Telegram and run `/newbot` **three times**
(one per bot). It gives you a token like `8123456789:AAF9x…` each time — that token *is*
the password to the bot, so treat it like one.

| Bot | Suggested name | Runs | Who talks to it |
|---|---|---|---|
| 1 | CLICKMINT Reward | `reward_bot.py` | every member |
| 2 | CLICKMINT Partnership | `partnership_bot.py` | members arranging partnerships |
| 3 | CLICKMINT Admin | `admin_bot.py` | you (and scoped admins) |

Then get **your own numeric user id** from [@userinfobot](https://t.me/userinfobot) —
it replies with a number like `123456789`. That number is what makes you the owner.

## Step 2 — put the secrets in `.env` (never in the code)
The bots read every secret from the environment (`config.py`). There is nothing to edit
inside any `.py` file — and pasting a token into one would push it to GitHub.

```bash
cp .env.example .env
nano .env          # or any editor
```
```ini
REWARD_BOT_TOKEN=8123456789:AAF9x…      # bot 1, from @BotFather
PARTNER_BOT_TOKEN=8234567890:AAG2y…     # bot 2  (note: PARTNER_, not PARTNERSHIP_)
ADMIN_BOT_TOKEN=8345678901:AAH3z…       # bot 3
OWNER_USER_ID=123456789                 # YOUR numeric id, from @userinfobot
# STORE_DIR=/var/lib/clickmint          # optional: keep the JSON data outside the repo
```
`.env` is git-ignored — confirm with `git check-ignore .env` (it should print `.env`).

On a host with a secrets UI (or systemd), you can skip the file and set the same four
variables as environment variables instead. The systemd units in this repo read
`/opt/clickmint/.env` via `EnvironmentFile=`.

## Step 3 — verify before you start anything
```bash
pip install -r requirements.txt
python3 preflight.py
```
It checks all four values, catches the classic mistakes (placeholder left in, the same
token pasted twice, owner id still 0, unwritable `STORE_DIR`), then asks Telegram to
confirm each token and prints the @username it belongs to:

```
✓ REWARD_BOT_TOKEN → @ClickMintRewardBot (CLICKMINT Reward) — reward_bot.py
READY.
```
Tokens are masked in the output, so it's safe to paste the result to someone for help.
Use `python3 preflight.py --offline` to skip the network part.

## Step 4 — run them
```bash
python3 reward_bot.py        # terminal 1
python3 partnership_bot.py   # terminal 2
python3 admin_bot.py         # terminal 3
```
Message bot 3 with `/start`: as the owner you should see the admin dashboard. If you see
"🔒 This is the owner's admin panel", `OWNER_USER_ID` doesn't match the account you're
messaging from.

Optional, once: push the approved names/descriptions/commands to Telegram —
```bash
python3 branding.py --check    # shows the copy, sends nothing
python3 branding.py --apply    # writes it to all three bots via the Bot API
```

## Step 5 — keep them alive (systemd)
Three unit files ship with the repo: `clickmint.service` (reward),
`clickmint-partner.service`, `clickmint-admin.service`. On a fresh Linux box the whole
thing is one command:

```bash
bash deploy.sh https://github.com/anthonyokoye-pro/clickmint-partner-bots.git
```
It installs Python, clones to `/opt/clickmint`, creates a venv, seeds `.env` from the
example (edit it, then re-run), **runs preflight and refuses to start if it fails**, then
enables and starts all three services. Logs:

```bash
sudo journalctl -u clickmint-reward  -f
sudo journalctl -u clickmint-partner -f
sudo journalctl -u clickmint-admin   -f
```
To update after a push: `cd /opt/clickmint && git pull && sudo systemctl restart clickmint-reward clickmint-partner clickmint-admin`

## Before you go live, run the offline suite once
```bash
./run_tests.sh     # compile · 28 engine · 33 governance · 26 wiring · branding · dry run
```
No token needed — it never touches the network.

## Backup (free, do this weekly)
The whole "database" is two files: `reward_ledger.json` and `partnership_state.json`
(in `STORE_DIR`). Copy them somewhere safe — that's your backup. Restoring is just
putting them back.

## If something goes wrong
| Symptom | Cause | Fix |
|---|---|---|
| `Unauthorized` on start | token wrong/revoked | `/token` in @BotFather, update `.env` |
| Bot ignores you completely | another copy is already polling that token | stop the duplicate — one process per token |
| You get the member menu, not the owner one | `OWNER_USER_ID` ≠ your account | re-check with @userinfobot |
| "conflict: terminated by other getUpdates" | two bots share one token | give each bot its own (preflight catches this) |
| Bot can't post into a channel | it isn't an admin there | add it as admin with **Post Messages** |
