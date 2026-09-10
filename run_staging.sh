#!/usr/bin/env bash
# Start ClickMint against the isolated staging environment only.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-$ROOT/.env.staging}"
cd "$ROOT"
python3 staging_preflight.py --env-file "$ENV_FILE"
set -a
. "$ENV_FILE"
set +a
exec bash run_bots.sh start
