#!/usr/bin/env bash
set -euo pipefail

WATCHDOG="$HOME/xtt-workflow/manager/watchdog.py"
SESSION_NAME="xtt-postprocess"
export XTT_TMUX_SESSION="$SESSION_NAME"
export XTT_LOOP_PID="$$"

while true; do
  python3 "$WATCHDOG" heartbeat-worker postprocess idle "" "$SESSION_NAME" "$$" >/dev/null 2>&1 || true
  python3 "$WATCHDOG" reconcile >/dev/null 2>&1 || true
  "$HOME/xtt-workflow/manager/workers/postprocess_done.sh" || true
  sleep 5
done
