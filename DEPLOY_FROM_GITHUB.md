# Put CLICKMINT on GitHub and deploy from there

This repo is **GitHub-ready**: no secrets in the code, tests run automatically on every
push, and it deploys to your own free server straight from GitHub.

## 1. Create the repo and push

```bash
cd /home/user/partner_bots
git init
git add -A
git commit -m "CLICKMINT partner bots — reward, partnership, admin panel"
git branch -M main
# create an empty repo on GitHub (no README/template), then:
git remote add origin https://github.com/YOU/clickmint-bots.git
git push -u origin main
```

`.gitignore` keeps the JSON store files, `.env`, and `__pycache__` out — so your bot
tokens and your member data never get published.

## 2. Secrets go in the Environment, not the repo

The three bots read tokens + owner id from **environment variables** (`config.py`). Never
paste them into code. Locally, copy the template and fill it in:

```bash
cp .env.example .env        # fill in tokens, OWNER_USER_ID, and CLICKMINT_CREDENTIAL_KEY
```

`.env` is git-ignored. `python-dotenv` (in `requirements.txt`) loads it automatically.
On a server, set the same vars in `/opt/clickmint/.env` (read by the systemd services).

## 3. Tests run automatically on every push

`.github/workflows/tests.yml` runs the full offline suite (`test_core.py` +
`test_governance.py` + a compile check) on every push and PR. If a change breaks the
logic, GitHub flags it — before it ever reaches production. You can watch the green/red
check on the repo's Actions tab.

## 4. Deploy from GitHub (pick ONE)

### Option A — your own server, deploy on push via SSH (recommended, $0)
Runs on Oracle Always Free / a Raspberry Pi / any VPS. `deploy.sh` clones or pulls the
repo, installs deps, and starts the bots as systemd services that auto-restart.

On the server (once):
```bash
bash deploy.sh https://github.com/YOU/clickmint-bots.git
nano /opt/clickmint/.env     # add real tokens + OWNER_USER_ID
sudo systemctl restart clickmint-reward clickmint-partner clickmint-admin
sudo journalctl -u clickmint-reward -f
```

Optional **auto-deploy on push** (`.github/workflows/deploy.yml`): see
[`docs/DEPLOY_WORKFLOW.md`](docs/DEPLOY_WORKFLOW.md) before enabling it. Set repo secrets
`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KEY` (an SSH key, public half added to the server's
`authorized_keys`). Then a push to `main` → GitHub SSHes in → `git pull` → restart all
three services. CI already ran the tests first.

### Option B — a container platform
These deploy straight from GitHub. **Caveat for a polling bot:** the host must keep a
long-running process alive. Render's free tier **sleeps after 15 min** (kills the bot);
Vercel/Lambda can't poll at all. Only pick a platform that runs a persistent process.

## 5. The three services (same repo, same host, one machine)
| Service | File | Env token |
|---|---|---|
| Reward bot | `reward_bot.py` | `REWARD_BOT_TOKEN` |
| Partnership bot | `partnership_bot.py` | `PARTNER_BOT_TOKEN` |
| Admin panel | `admin_bot.py` | `ADMIN_BOT_TOKEN` |

`OWNER_USER_ID` (your numeric id) is the key to the owner/admin/user menus in all three.
Keep `STORE_DIR` identical across all three so the admin panel sees the network's data.

## 6. Deploying when you already have money/later
When you outgrow this, you move the store from JSON/SQLite to Postgres (Supabase free
tier or a small VPS), run on a cheap paid host, and keep the same GitHub repo — the code
doesn't change, only the storage layer. You're set up to scale without rewriting.
