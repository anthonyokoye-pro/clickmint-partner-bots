# CLICKMINT — run everything for $0/month

## The key misconception (read this first)
> "I can't afford to host all these bots." — **You don't pay per bot.**
> One single free server runs the reward bot, the partnership bot, and any future
> admin panel **all at the same time.** Each bot is just one Python process on the
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
3. **Run both bots** as background services with `systemd` (see `clickmint.service`).
   Each bot = one process on the SAME machine → **no per-bot cost.**
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

## What you have to fill in (in both bot files)
```python
BOT_TOKEN = "your_bot_token"    # from @BotFather
OWNER_USER_ID = 123456789       # YOUR numeric Telegram id (the one who owns it)
```

---

## Run it
```bash
cd /home/user/partner_bots
pip install -r requirements.txt
python reward_bot.py        # terminal test first
python partnership_bot.py   # second terminal / two services
```
Or run as services that auto-start (see `clickmint.service`).

## Backup (free, do this weekly)
The whole "database" is 2 files: `reward_ledger.json` and `partnership_state.json`.
Copy them somewhere safe (Google Drive, a second device). That's your backup.
