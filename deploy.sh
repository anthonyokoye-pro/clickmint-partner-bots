#!/usr/bin/env bash
# CLICKMINT — one-shot deploy to a Linux box (Oracle Always Free / Pi / any VPS).
# Deploys FROM GITHUB by cloning/pulling the repo, installing deps, and starting
# the three bots as systemd services. Idempotent: safe to re-run on every release.
#
# Usage on the server:
#   bash deploy.sh https://github.com/<YOU>/<REPO>.git
#
# After the first run, put your real tokens in /opt/clickmint/.env (cp .env.example .env).
set -euo pipefail

REPO="${1:-https://github.com/YOU/clickmint-bots.git}"   # <-- your repo URL
APP_DIR="/opt/clickmint"
APP_USER="clickmint"

echo "==> Installing system packages (python3, pip, git)"
sudo apt-get update -y && sudo apt-get install -y python3 python3-pip python3-venv git

echo "==> Creating app user + dir"
sudo useradd -m "$APP_USER" 2>/dev/null || true
sudo mkdir -p "$APP_DIR" && sudo chown "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> Deploying from GitHub: $REPO"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only || true
else
  sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
fi

echo "==> Installing Python deps (as $APP_USER)"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Seeding .env if missing (then edit it with your real tokens)"
if [ ! -f "$APP_DIR/.env" ]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "!! Edit $APP_DIR/.env with your real bot tokens + OWNER_USER_ID."
fi

echo "==> Preflight: are the secrets actually usable?"
if ! sudo -u "$APP_USER" env -C "$APP_DIR" "$APP_DIR/.venv/bin/python3" preflight.py; then
  echo "!! Preflight failed. Fix $APP_DIR/.env, then re-run this script."
  echo "   (Nothing was started, so the bots are not running with a bad config.)"
  exit 1
fi

echo "==> Installing systemd services (all THREE bots)"
sudo cp "$APP_DIR/clickmint.service" "$APP_DIR/clickmint-partner.service" \
        "$APP_DIR/clickmint-admin.service" /etc/systemd/system/
# point at the venv python
sudo sed -i "s#/usr/bin/python3#$APP_DIR/.venv/bin/python3#" \
     /etc/systemd/system/clickmint.service \
     /etc/systemd/system/clickmint-partner.service \
     /etc/systemd/system/clickmint-admin.service || true
sudo systemctl daemon-reload
sudo systemctl enable clickmint-reward clickmint-partner clickmint-admin 2>/dev/null || true
sudo systemctl restart clickmint-reward clickmint-partner clickmint-admin

echo "==> Done. Check logs:"
echo "    sudo journalctl -u clickmint-reward  -f"
echo "    sudo journalctl -u clickmint-partner -f"
echo "    sudo journalctl -u clickmint-admin   -f"
