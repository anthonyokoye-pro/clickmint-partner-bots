#!/usr/bin/env bash
# CLICKMINT — guided setup.
#
# For people who don't live in a terminal. Run it once:
#
#     bash setup.sh
#
# It asks you four questions (three bot tokens + your Telegram id), checks every
# answer as you type it, writes them to a private .env file, installs what's
# needed, and confirms with Telegram that the tokens really work. It changes
# nothing else and never prints a full token.

# Colours (ignored if the terminal doesn't support them)
B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; D=$'\033[2m'; N=$'\033[0m'
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
err()  { printf '%s✗%s %s\n' "$R" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
rule() { printf '%s\n' "${D}──────────────────────────────────────────────────────────${N}"; }

say ""
say "${B}CLICKMINT setup${N}"
say "I'll ask four questions. Paste the answer and press Enter after each one."
say "Nothing is sent anywhere except a check with Telegram at the end."
rule

# ---------------------------------------------------------------- python
if command -v python3 >/dev/null 2>&1; then
  ok "Python found: $(python3 --version 2>&1)"
else
  err "Python 3 is not installed on this machine."
  say "  Install it first:"
  say "    Ubuntu/Debian/Raspberry Pi:  sudo apt install python3 python3-pip"
  say "    Mac:                         brew install python3"
  say "    Windows:                     https://www.python.org/downloads/"
  exit 1
fi

# ---------------------------------------------------------------- existing .env
if [ -f .env ]; then
  warn "A .env file already exists (your current settings)."
  read -r -p "   Replace it? Type yes to replace, anything else to keep: " REPLY_ENV
  if [ "$REPLY_ENV" != "yes" ]; then
    say "Keeping your existing .env. Skipping to the check."
    KEEP_ENV=1
  else
    cp .env ".env.backup.$(date +%s)" 2>/dev/null && \
      ok "Old settings backed up as .env.backup.*"
  fi
fi

# ---------------------------------------------------------------- questions
ask_token() {
  # $1 = human name, $2 = variable name, $3 = which script uses it
  local human="$1" var="$2" script="$3" value=""
  while true; do
    say ""
    say "${B}$human${N}"
    say "${D}  In Telegram, open @BotFather → /newbot → copy the long token it gives you."
    say "  It looks like:  8123456789:AAF9xQ2mKp7vLzR3tYuIoP1aSdFgHjKlZxC"
    say "  (this one runs $script)${N}"
    # Never echo bot tokens into the terminal or a captured shell transcript.
    # Bash's -s works in Git Bash as well as native Unix shells.
    if [ -t 0 ] && [ -t 1 ]; then
      read -r -s -p "  Paste the token here: " value
      printf '\n'
    else
      # Keep setup usable from automation/non-interactive wrappers. The caller
      # is responsible for protecting stdin in that case.
      read -r value
    fi
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    if [ -z "$value" ]; then
      err "Nothing entered — try again."
    elif printf '%s' "$value" | grep -Eq '^[0-9]{6,}:[A-Za-z0-9_-]{30,}$'; then
      ok "Looks like a valid token."
      printf -v "$var" '%s' "$value"
      return 0
    else
      err "That doesn't look like a BotFather token."
      say "   ${D}It must be digits, then a colon, then a long code. Copy the WHOLE line.${N}"
    fi
  done
}

if [ "${KEEP_ENV:-0}" != "1" ]; then
  ask_token "Question 1 of 4 — the REWARD bot's token"      TOK_REWARD  "reward_bot.py"
  ask_token "Question 2 of 4 — the PARTNERSHIP bot's token" TOK_PARTNER "partnership_bot.py"
  ask_token "Question 3 of 4 — the ADMIN bot's token"       TOK_ADMIN   "admin_bot.py"

  # three different bots, three different tokens
  if [ "$TOK_REWARD" = "$TOK_PARTNER" ] || [ "$TOK_REWARD" = "$TOK_ADMIN" ] || \
     [ "$TOK_PARTNER" = "$TOK_ADMIN" ]; then
    say ""
    err "Two of those tokens are identical."
    say "   Each bot needs its OWN token, or they fight over the same messages."
    say "   Run 'bash setup.sh' again with three different tokens."
    exit 1
  fi

  while true; do
    say ""
    say "${B}Question 4 of 4 — your Telegram user id${N}"
    say "${D}  In Telegram, open @userinfobot and send it any message."
    say "  It replies with a number like 123456789. That number makes YOU the owner:"
    say "  no credit costs, no daily cap, and the only one who can invite admins.${N}"
    read -r -p "  Type the number here: " OWNER_ID
    OWNER_ID="$(printf '%s' "$OWNER_ID" | tr -d '[:space:]')"
    if printf '%s' "$OWNER_ID" | grep -Eq '^[0-9]+$' && [ "$OWNER_ID" -gt 0 ] 2>/dev/null; then
      ok "Owner id accepted."
      break
    fi
    err "That must be digits only (no @, no spaces, no minus sign)."
  done

  # Generate the encryption key locally; it protects user-owned Telegram bot
  # tokens and must never be sent to Telegram or committed to Git.
  CREDENTIAL_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

  # ------------------------------------------------------------- write .env
  {
    echo "# Written by setup.sh on $(date -u '+%Y-%m-%d %H:%M UTC')."
    echo "# This file holds your SECRETS. It is git-ignored — never share or commit it."
    echo "REWARD_BOT_TOKEN=$TOK_REWARD"
    echo "PARTNER_BOT_TOKEN=$TOK_PARTNER"
    echo "ADMIN_BOT_TOKEN=$TOK_ADMIN"
    echo "OWNER_USER_ID=$OWNER_ID"
    echo "CLICKMINT_CREDENTIAL_KEY=$CREDENTIAL_KEY"
  } > .env
  chmod 600 .env 2>/dev/null
  say ""
  ok "Saved to .env (readable only by you)."
fi

# ---------------------------------------------------------------- deps
rule
say "${B}Installing what the bots need${N} ${D}(aiogram — this can take a minute)${N}"
if python3 -c "import aiogram" >/dev/null 2>&1; then
  ok "Already installed."
else
  if python3 -m pip install -q -r requirements.txt 2>/dev/null; then
    ok "Installed."
  elif python3 -m pip install -q --user -r requirements.txt 2>/dev/null; then
    ok "Installed (for your user)."
  elif python3 -m pip install -q --break-system-packages -r requirements.txt 2>/dev/null; then
    ok "Installed."
  else
    err "Could not install automatically."
    say "   Try this one line, then run setup.sh again:"
    say "     python3 -m pip install --user -r requirements.txt"
    exit 1
  fi
fi

# ---------------------------------------------------------------- verify
rule
say "${B}Checking your setup${N}"
say ""
if python3 preflight.py; then
  rule
  say "${G}${B}You're ready.${N}"
  say ""
  say "Start all three bots:      ${B}bash run_bots.sh start${N}"
  say "See if they're running:    ${B}bash run_bots.sh status${N}"
  say "Watch what they're doing:  ${B}bash run_bots.sh logs${N}"
  say "Stop them:                 ${B}bash run_bots.sh stop${N}"
  say ""
  say "${D}Then open your admin bot in Telegram and send /start.${N}"
else
  rule
  err "Something above needs fixing. Each ✗ line says exactly what to do."
  say "   When you've fixed it, run ${B}bash setup.sh${N} again."
  exit 1
fi
