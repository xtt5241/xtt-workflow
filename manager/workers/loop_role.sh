#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:?role required}"
SESSION_NAME="xtt-$ROLE"
WATCHDOG="$HOME/xtt-workflow/manager/watchdog.py"
export XTT_TMUX_SESSION="$SESSION_NAME"
export XTT_LOOP_PID="$$"

while true; do
  python3 "$WATCHDOG" heartbeat-worker "$ROLE" idle "" "$SESSION_NAME" "$$" >/dev/null 2>&1 || true
  "$HOME/xtt-workflow/manager/workers/run_one_task.sh" "$ROLE" || true
  python3 "$WATCHDOG" heartbeat-worker "$ROLE" idle "" "$SESSION_NAME" "$$" >/dev/null 2>&1 || true
  sleep 5
done
