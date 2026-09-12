#!/usr/bin/env bash
# CLICKMINT — start / stop / check the three bots without needing three terminals.
#
#     bash run_bots.sh start     start all three in the background
#     bash run_bots.sh stop      stop them
#     bash run_bots.sh status    are they running?
#     bash run_bots.sh logs      watch what they're doing (Ctrl-C to stop watching)
#     bash run_bots.sh restart   stop, then start
#
# Logs are written to logs/<bot>.log. This is the simple option for a laptop or
# Pi; on a real server use the systemd services instead (see DEPLOY_FREE.md).

B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE" || exit 1
mkdir -p logs run

BOTS="reward:reward_bot.py partner:partnership_bot.py admin:admin_bot.py"

pid_of() { [ -f "run/$1.pid" ] && cat "run/$1.pid" 2>/dev/null; }
alive()  { local p; p="$(pid_of "$1")"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

start_one() {
  local name="$1" script="$2"
  if alive "$name"; then
    printf '%s·%s %-8s already running (pid %s)\n' "$D" "$N" "$name" "$(pid_of "$name")"
    return
  fi
  nohup python3 "$script" >>"logs/$name.log" 2>&1 &
  echo $! > "run/$name.pid"
}

# A bot can start and then die a second later (bad token, no internet). Check
# again after a pause and show the reason instead of claiming success.
verify_one() {
  local name="$1"
  if alive "$name"; then
    printf '%s✓%s %-8s running (pid %s) → logs/%s.log\n' "$G" "$N" "$name" "$(pid_of "$name")" "$name"
  else
    printf '%s✗%s %-8s did NOT stay running. Why:\n' "$R" "$N" "$name"
    grep -iE "Unauthorized|TelegramNetworkError|Conflict|Error" "logs/$name.log" 2>/dev/null \
      | tail -n 2 | cut -c1-150 | sed 's/^/      /'
    printf '      %sFull details: logs/%s.log%s\n' "$D" "$name" "$N"
    rm -f "run/$name.pid"
  fi
}

stop_one() {
  local name="$1" p
  p="$(pid_of "$name")"
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
    kill "$p" 2>/dev/null
    for _ in 1 2 3 4 5; do kill -0 "$p" 2>/dev/null || break; sleep 1; done
    kill -9 "$p" 2>/dev/null
    printf '%s✓%s %-8s stopped\n' "$G" "$N" "$name"
  else
    printf '%s·%s %-8s was not running\n' "$D" "$N" "$name"
  fi
  rm -f "run/$name.pid"
}

case "${1:-}" in
  start)
    if [ ! -f .env ]; then
      printf '%s✗%s No .env file — run %sbash setup.sh%s first.\n' "$R" "$N" "$B" "$N"
      exit 1
    fi
    if ! python3 -c 'import config; from launch_scope import assert_nonfinancial_scope; assert_nonfinancial_scope(config)' 2>"logs/scope.log"; then
      printf '%s✗%s Financial features are enabled; launch scope is non-financial. See logs/scope.log.\n' "$R" "$N"
      exit 1
    fi
    printf '%sStarting CLICKMINT%s\n' "$B" "$N"
    for pair in $BOTS; do start_one "${pair%%:*}" "${pair##*:}"; done
    printf '%s  (giving them a few seconds to connect to Telegram...)%s\n' "$D" "$N"
    sleep 4
    for pair in $BOTS; do verify_one "${pair%%:*}"; done
    printf '\n%sWatch them with:%s bash run_bots.sh logs\n' "$D" "$N"
    ;;
  stop)
    printf '%sStopping CLICKMINT%s\n' "$B" "$N"
    for pair in $BOTS; do stop_one "${pair%%:*}"; done
    ;;
  restart)
    "$0" stop; echo; "$0" start
    ;;
  status)
    printf '%sCLICKMINT status%s\n' "$B" "$N"
    for pair in $BOTS; do
      name="${pair%%:*}"
      if alive "$name"; then
        printf '%s✓%s %-8s running (pid %s)\n' "$G" "$N" "$name" "$(pid_of "$name")"
      else
        printf '%s✗%s %-8s stopped\n' "$R" "$N" "$name"
      fi
    done
    ;;
  logs)
    printf '%sShowing what the bots are doing. Press Ctrl-C to stop watching%s\n' "$D" "$N"
    printf '%s(this does NOT stop the bots)%s\n\n' "$D" "$N"
    tail -n 20 -f logs/*.log
    ;;
  *)
    cat <<EOF
${B}CLICKMINT bot control${N}

  bash run_bots.sh start     start all three bots in the background
  bash run_bots.sh stop      stop them
  bash run_bots.sh restart   stop, then start again
  bash run_bots.sh status    check whether they're running
  bash run_bots.sh logs      watch what they're doing (Ctrl-C to stop watching)

First time here? Run ${B}bash setup.sh${N} — it asks for your tokens and sets everything up.
EOF
    ;;
esac
