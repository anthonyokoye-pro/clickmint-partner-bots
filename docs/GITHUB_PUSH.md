# How to Push CLICKMINT to GitHub with Git Bash (detailed, beginner-friendly)

This walks you through every step, assumes you're on **Windows with Git Bash**, and shows
the exact commands plus the common errors and how to fix them. The same commands work on
Mac/Linux and on Termux (Android) — only the "install" part differs.

---

## STEP 0 — The most important thing (read this first)
> Git Bash must be **in the folder that contains the code.** If you run the commands in the
> wrong folder, either nothing happens or you push an empty/other repo.

The folder is wherever you put the files. In the AI session, the repo files live under the
folder `partner_bots` (contains `reward_bot.py`, `core.py`, `config.py`, `.gitignore`,
`.env.example`, the workflows, etc.). Put that whole folder on your machine first.

To check you're in the right place in Git Bash:
```bash
pwd            # prints the current folder path
ls             # lists files — you should see reward_bot.py, core.py, .env.example...
```

---

## STEP 1 — Install Git (Windows Git Bash)
If you don't have Git yet:
1. Go to https://git-scm.com/downloads
2. Download "64-bit Git for Windows Setup."
3. Run the installer. **Accept the defaults** (it includes "Git Bash").
4. Open **Git Bash** (Start menu → search "Git Bash").
5. Verify:
   ```bash
   git --version       # e.g. git version 2.4x.x
   ```

---

## STEP 2 — Create the empty GitHub repository (on github.com)
1. Log in at https://github.com
2. Top-right **＋** → **New repository**.
3. **Repository name:** `clickmint-bots` (or `clickmint-partner-bots` — your call).
4. **Public** (fine — no secrets in the repo; Actions free-tier works) **or Private** (fine too).
5. **Do NOT** tick "Add a README / .gitignore / license" — leave it **empty**. (Your repo already
   has its own README and .gitignore; starting empty avoids a merge conflict.)
6. Click **Create repository**.
7. Copy the repo URL shown:
   - **HTTPS:** `https://github.com/<YOUR-USERNAME>/clickmint-bots.git`  ← easiest
   - **SSH:** `git@github.com:<YOUR-USERNAME>/clickmint-bots.git`

---

## STEP 3 — Open Git Bash in the code folder
Easiest ways on Windows:
- Open the folder in File Explorer → right-click empty space → **"Git Bash Here"**, or
- In Git Bash: `cd "C:/path/to/partner_bots"` (use forward slashes; quotes if spaces).

Confirm you see the files:
```bash
ls
#  .env.example   .github   .gitignore   README.md   admin_bot.py   core.py
#  governance.py  ...  reward_bot.py  ...
```

---

## STEP 4 — Authenticate with GitHub (one-time, choose ONE method)

### Option A — HTTPS + Personal Access Token (recommended for beginners)
1. On github.com: click your avatar → **Settings** → **Developer settings** → **Personal
   access tokens** → **Tokens (classic)** → **Generate new token (classic)**.
2. Give it a name, set **Expiration** (e.g. 90 days), and tick the **`repo`** scope (full).
3. Click **Generate** → **copy the token** (it shows once — save it somewhere safe).
4. In Git Bash (set identity if not already):
   ```bash
   git config --global user.name  "Your Name"
   git config --global user.email "you@example.com"
   ```

### Option B — SSH (no token on each push, but more setup)
Skip unless you prefer SSH. (Needs `ssh-keygen`, adding the public key to github.com
Settings → SSH keys.)

> For this project, **Option A (HTTPS + token) is simplest.**

---

## STEP 5 — Git Hub commands (run in order)
Now, in Git Bash inside the code folder:

```bash
# 1) Turn this folder into a git repo
git init

# 2) Stage ALL files (except what .gitignore excludes: secrets, JSON data, caches)
git add -A

# 3) Check what will be committed (sanity: .env and *.json should NOT appear)
git status
#   if you accidentally see "reward_ledger.json" or "partnership_state.json" staged,
#   that means .gitignore isn't being read — but it should be. (See Troubleshooting.)

# 4) Commit with a message
git commit -m "CLICKMINT partner bots v1"

# 5) Rename the default branch to main
git branch -M main

# 6) Point local repo at your GitHub repo
git remote add origin https://github.com/<YOUR-USERNAME>/clickmint-bots.git

# 7) Link and push the first time (-u remembers origin/main for future pushes)
git push -u origin main
```

**On the first push** Git asks for a username and **password**. For the password,
**paste the Personal Access Token** (not your GitHub account password). Token is the
password. (You can also use `git push -u https://<USERNAME>:<TOKEN>@github.com/...` but
that leaves the token in git history / config — better to type it when prompted.)

---

## STEP 6 — Verify it worked
After the push, check one of:
```bash
git remote -v                  # shows your origin URL
git log --oneline              # shows your commit
git status                     # "Your branch is up to date with 'origin/main'"
```
And on github.com, reload the repo page — you should see the files, and (if public) the
**Tests** workflow starting under the **Actions** tab. It turns green when the 14 + 23 tests pass.

---

## STEP 7 — After the first push (future updates)
Every time you change code and want to push again:
```bash
git add -A
git commit -m "describe what you changed"
git push            # 'origin main' is remembered from the first push
```

---

## Troubleshooting (the errors everyone hits)
| Error | Meaning / Fix |
|---|---|
| `fatal: not a git repository` | You're in the wrong folder. `cd` into the folder that has `.git` or re-do Step 3. |
| `remote origin already exists` | You ran `git remote add` twice. Use `git remote set-url origin <URL>` instead. |
| `Permission denied (publickey)` | Using SSH without a key / wrong key. Use HTTPS + token (Option A). |
| `Authentication failed` / `Support for password auth removed` | Git asked for a password — paste your **Personal Access Token**, not your account password. |
| "empty repository"/"nothing to push" | You committed in the wrong folder, or created the GitHub repo with a README that now conflicts. Check `ls` shows the code; if the repo has a README, do `git pull origin main --rebase` then `git push`. |
| A `.env` or `*.json` shows in `git status` | `.gitignore` isn't taking effect. Remove it from staging: `git rm --cached reward_ledger.json` (and other data files), then re-commit. |
| `Updates were rejected because the remote contains work` | Remote has commits (e.g. you added a README on GitHub). `git pull origin main --rebase`, resolve, then `git push`. |
| Push works but branch is `master` | Run `git branch -M main` and push again. |

---

## Safety reminders
- **Never** commit `.env` or the `.json` data files (they hold your bot tokens + member data).
  `.gitignore` handles this — confirm with `git status` before pushing.
- The **username** that's baked in is your Github username; it's in the URL only, not in code.
- Bot **tokens** are only ever in your local `.env` / server env, never in the repo.

---

## Quick copy-paste for the next time (all at once)
```bash
cd /path/to/partner_bots
git init
git add -A
git status
git commit -m "CLICKMINT partner bots v1"
git branch -M main
git remote add origin https://github.com/<YOUR-USERNAME>/clickmint-bots.git
git push -u origin main
```
