#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/xtt-workflow"
QUEUE_PENDING="$ROOT/manager/queue/pending"
QUEUE_RUNNING="$ROOT/manager/queue/running"
QUEUE_DONE="$ROOT/manager/queue/done"
QUEUE_FAILED="$ROOT/manager/queue/failed"
QUEUE_NEEDS_HUMAN="$ROOT/manager/queue/needs-human"
STATE_DIR="$ROOT/manager/state"
LOG_DIR="$ROOT/manager/logs"
RESULTS_DIR="$ROOT/manager/results"

ROLE="${1:?role required}"
mkdir -p "$QUEUE_PENDING" "$QUEUE_RUNNING" "$QUEUE_DONE" "$QUEUE_FAILED" "$QUEUE_NEEDS_HUMAN" "$STATE_DIR" "$LOG_DIR" "$RESULTS_DIR"

TASK_FILE="$({
  find "$QUEUE_PENDING" -maxdepth 1 -type f -name '*.json' | sort | while read -r f; do
    [ "$(jq -r '.role' "$f")" = "$ROLE" ] || continue
    deps_ok=1
    for dep in $(jq -r '.depends_on[]?' "$f"); do
      [ -f "$QUEUE_DONE/$dep.json" ] || deps_ok=0
    done
    [ "$deps_ok" = 1 ] && echo "$f" && break
  done
} | head -n 1)"

[ -z "$TASK_FILE" ] && exit 0

BASENAME="$(basename "$TASK_FILE")"
RUNNING_FILE="$QUEUE_RUNNING/$BASENAME"
mv "$TASK_FILE" "$RUNNING_FILE"

TASK_ID="$(jq -r '.id' "$RUNNING_FILE")"
TITLE="$(jq -r '.title' "$RUNNING_FILE")"
LOG_PATH="$LOG_DIR/${TASK_ID}.log"

if ! python3 "$ROOT/manager/task_schema.py" validate "$RUNNING_FILE" >> "$LOG_PATH" 2>&1; then
  fail_task
fi

REPO="$(jq -r '.repo' "$RUNNING_FILE")"
TYPE="$(jq -r '.type' "$RUNNING_FILE")"
WORKTREE="$(jq -r '.worktree' "$RUNNING_FILE")"
BRANCH="$(jq -r '.branch' "$RUNNING_FILE")"
BASE_BRANCH="$(jq -r '.base_branch' "$RUNNING_FILE")"
SOURCE_REF="$(jq -r '.source_ref // empty' "$RUNNING_FILE")"
PROMPT_FILE="$(jq -r '.prompt_file' "$RUNNING_FILE")"
RETRY_COUNT="$(jq -r '.retry_count // 0' "$RUNNING_FILE")"
ALLOW_AUTO_COMMIT="$(jq -r '.allow_auto_commit' "$RUNNING_FILE")"
printf 'repo profile: %s\n' "$(jq -c '.repo_profile // {}' "$RUNNING_FILE")" >> "$LOG_PATH"

next_retry_branch() {
  python3 - "$1" "$2" <<'PY'
import re
import sys
import time

branch = (sys.argv[1] or "retry").strip() or "retry"
retry_count = int(sys.argv[2]) + 1
stamp = int(time.time())

def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "project"

if "/" in branch:
    prefix, rest = branch.split("/", 1)
    print(f"{prefix}/{safe_name(rest)}-retry-{retry_count}-{stamp}")
else:
    print(f"{safe_name(branch)}-retry-{retry_count}-{stamp}")
PY
}

log_git_diff_context() {
  if [ "$ROLE" != "reviewer" ] && [ "$ROLE" != "verifier" ]; then
    return 0
  fi

  {
    printf -- '--- git diff stat vs origin/%s ---\n' "$BASE_BRANCH"
    git diff --stat "origin/$BASE_BRANCH...HEAD"
    printf '\n'
    printf -- '--- git diff names vs origin/%s ---\n' "$BASE_BRANCH"
    git diff --name-only "origin/$BASE_BRANCH...HEAD"
    printf '\n'
  } >> "$LOG_PATH" 2>&1 || true
}

REPO_MAIN="$ROOT/workspace/$REPO"
WORKTREE_PATH="$ROOT/workspace/$WORKTREE"
PROMPT_PATH="$ROOT/manager/prompts/$PROMPT_FILE"
PROMPT_TMP="$ROOT/manager/state/${TASK_ID}.prompt.md"
BUDGET_REPORT_PATH="$ROOT/manager/state/${TASK_ID}.budget.json"
CHANGE_RISK_REPORT_PATH="$ROOT/manager/state/${TASK_ID}.risk.json"
CODEX_HOME_PATH="$ROOT/codex-homes/$ROLE"
RESULT_PATH="$RESULTS_DIR/${TASK_ID}.json"
WATCHDOG_SCRIPT="$ROOT/manager/watchdog.py"
TASK_HEARTBEAT_PID=""

write_result() {
  local task_path="${1:-$RUNNING_FILE}"
  python3 "$ROOT/manager/result_writer.py" write "$task_path" "$LOG_PATH" "$RESULT_PATH" >/dev/null 2>&1 || true
}

start_task_watchdog() {
  python3 "$WATCHDOG_SCRIPT" start-task "$RUNNING_FILE" "$LOG_PATH" "${XTT_TMUX_SESSION:-}" "${XTT_LOOP_PID:-}" "$$" >/dev/null 2>&1 || true
  (
    while true; do
      python3 "$WATCHDOG_SCRIPT" heartbeat-task "$RUNNING_FILE" "$LOG_PATH" "${XTT_TMUX_SESSION:-}" "${XTT_LOOP_PID:-}" "$$" >/dev/null 2>&1 || true
      sleep 15
    done
  ) &
  TASK_HEARTBEAT_PID="$!"
}

stop_task_watchdog() {
  if [ -n "$TASK_HEARTBEAT_PID" ]; then
    kill "$TASK_HEARTBEAT_PID" >/dev/null 2>&1 || true
    wait "$TASK_HEARTBEAT_PID" >/dev/null 2>&1 || true
    TASK_HEARTBEAT_PID=""
  fi
}

finish_task_watchdog() {
  local final_queue="$1"
  local final_status="$2"
  stop_task_watchdog
  python3 "$WATCHDOG_SCRIPT" finish-task "$TASK_ID" "$final_queue" "$final_status" "$ROLE" >/dev/null 2>&1 || true
}

trap stop_task_watchdog EXIT

running_lifecycle_state() {
  case "$TYPE" in
    build) printf 'building' ;;
    review) printf 'reviewing' ;;
    verify) printf 'verifying' ;;
    *) printf 'routed' ;;
  esac
}

done_lifecycle_state() {
  case "$TYPE" in
    build) printf 'build-done' ;;
    review) printf 'review-done' ;;
    verify) printf 'verify-done' ;;
    *) printf 'delivered' ;;
  esac
}

failed_lifecycle_state() {
  case "$TYPE" in
    build) printf 'failed-build' ;;
    review) printf 'failed-review' ;;
    verify) printf 'failed-verify' ;;
    *) printf 'failed-postprocess' ;;
  esac
}

RUNNING_LIFECYCLE="$(running_lifecycle_state)"
DONE_LIFECYCLE="$(done_lifecycle_state)"
FAILED_LIFECYCLE="$(failed_lifecycle_state)"

jq --arg lifecycle "$RUNNING_LIFECYCLE" '.status = "running" | .lifecycle_state = $lifecycle' "$RUNNING_FILE" > "$RUNNING_FILE.tmp" && mv "$RUNNING_FILE.tmp" "$RUNNING_FILE"

fail_task() {
  if [ -f "$RUNNING_FILE" ]; then
    jq --arg lifecycle "$FAILED_LIFECYCLE" '.status = "failed" | .lifecycle_state = $lifecycle' "$RUNNING_FILE" > "$RUNNING_FILE.tmp" && mv "$RUNNING_FILE.tmp" "$RUNNING_FILE"
    write_result "$RUNNING_FILE"
  fi
  finish_task_watchdog "failed" "failed"
  mv "$RUNNING_FILE" "$QUEUE_FAILED/$BASENAME"
  exit 1
}

move_to_needs_human() {
  local gate="$1"
  local report_path="$2"

  if [ -f "$report_path" ]; then
    jq --slurpfile report "$report_path" --arg gate "$gate" --arg lifecycle "$DONE_LIFECYCLE" --arg updated_at "$(date '+%Y-%m-%d %H:%M:%S')"        '.status = "needs-human"
        | .lifecycle_state = $lifecycle
        | .human_gate = $gate
        | .human_reason = (($report[0].summary // "needs human review"))
        | .budget_report = (if $gate == "change-budget" then ($report[0] // {}) else (.budget_report // {}) end)
        | .env_risk_report = (if $gate == "change-risk" then ($report[0] // {}) else (.env_risk_report // {}) end)
        | .env_risk_summary = (if $gate == "change-risk" then ($report[0].summary // (.env_risk_summary // "none detected")) else (.env_risk_summary // "none detected") end)
        | .risk_signals = (if $gate == "change-risk" then ($report[0].categories // (.risk_signals // [])) else (.risk_signals // []) end)
        | .risk_level = (if $gate == "change-risk" and (($report[0].risk_level // "") != "") then $report[0].risk_level else (.risk_level // "medium") end)
        | .human_updated_at = $updated_at' "$RUNNING_FILE" > "$RUNNING_FILE.tmp"
  else
    jq --arg gate "$gate" --arg lifecycle "$DONE_LIFECYCLE" --arg updated_at "$(date '+%Y-%m-%d %H:%M:%S')"        '.status = "needs-human"
        | .lifecycle_state = $lifecycle
        | .human_gate = $gate
        | .human_reason = "needs human review"
        | .human_updated_at = $updated_at' "$RUNNING_FILE" > "$RUNNING_FILE.tmp"
  fi
  mv "$RUNNING_FILE.tmp" "$RUNNING_FILE"
  write_result "$RUNNING_FILE"
  finish_task_watchdog "needs-human" "needs-human"
  mv "$RUNNING_FILE" "$QUEUE_NEEDS_HUMAN/$BASENAME"
  printf 'task moved to needs-human queue: %s\n' "$QUEUE_NEEDS_HUMAN/$BASENAME" >> "$LOG_PATH"
  exit 0
}

apply_change_risk_report() {
  local report_path="$1"
  [ -f "$report_path" ] || return 0

  jq --slurpfile report "$report_path"      '.risk_level = (if (($report[0].risk_level // "") != "") then $report[0].risk_level else (.risk_level // "medium") end)
      | .risk_signals = ($report[0].categories // (.risk_signals // []))
      | .env_risk_summary = ($report[0].summary // (.env_risk_summary // "none detected"))
      | .env_risk_report = ($report[0] // (.env_risk_report // {}))
      | .evidence_required = (((.evidence_required // []) + ($report[0].required_evidence // [])) | unique)' "$RUNNING_FILE" > "$RUNNING_FILE.tmp"
  mv "$RUNNING_FILE.tmp" "$RUNNING_FILE"
  python3 "$ROOT/manager/task_schema.py" normalize "$RUNNING_FILE" --in-place >/dev/null
  python3 "$ROOT/manager/test_strategy.py" apply "$RUNNING_FILE" --in-place >/dev/null
}

ensure_git_identity() {
  if ! git config user.name >/dev/null; then
    git config user.name "xtt workflow builder"
  fi
  if ! git config user.email >/dev/null; then
    git config user.email "xtt-workflow@local"
  fi
}

commit_builder_changes() {
  if [ "$ROLE" != "builder" ]; then
    return 0
  fi

  if [ "$ALLOW_AUTO_COMMIT" != "true" ]; then
    printf 'builder: allow_auto_commit=false, skip commit\n' >> "$LOG_PATH"
    return 0
  fi

  python3 "$ROOT/manager/builder_hygiene.py" clean-untracked "$PWD" >> "$LOG_PATH" 2>&1 || true

  if [ -z "$(git status --porcelain)" ]; then
    printf 'builder: no changes to commit after hygiene cleanup\n' >> "$LOG_PATH"
    return 0
  fi

  ensure_git_identity
  git add -A >> "$LOG_PATH" 2>&1
  python3 "$ROOT/manager/builder_hygiene.py" drop-staged "$PWD" >> "$LOG_PATH" 2>&1 || true

  if ! python3 "$ROOT/manager/task_boundary.py" check-staged "$RUNNING_FILE" "$PWD" >> "$LOG_PATH" 2>&1; then
    printf 'builder: boundary conflict detected, task will fail\n' >> "$LOG_PATH"
    return 1
  fi

  if [ -z "$(git diff --cached --name-only)" ]; then
    printf 'builder: no staged changes remain after hygiene cleanup\n' >> "$LOG_PATH"
    return 0
  fi

  CHANGE_RISK_EXIT=0
  set +e
  python3 "$ROOT/manager/change_risk.py" check-staged "$RUNNING_FILE" "$PWD" "$CHANGE_RISK_REPORT_PATH" >> "$LOG_PATH" 2>&1
  CHANGE_RISK_EXIT=$?
  set -e
  if [ "$CHANGE_RISK_EXIT" -ne 0 ] && [ "$CHANGE_RISK_EXIT" -ne 3 ]; then
    printf 'builder: change risk analysis failed\n' >> "$LOG_PATH"
    return 1
  fi
  if [ -f "$CHANGE_RISK_REPORT_PATH" ]; then
    apply_change_risk_report "$CHANGE_RISK_REPORT_PATH"
  fi

  set +e
  python3 "$ROOT/manager/change_budget.py" check-staged "$RUNNING_FILE" "$PWD" "$BUDGET_REPORT_PATH" >> "$LOG_PATH" 2>&1
  BUDGET_EXIT=$?
  set -e
  if [ "$BUDGET_EXIT" -eq 3 ]; then
    ensure_git_identity
    git commit -m "builder: ${TASK_ID} ${TITLE}" >> "$LOG_PATH" 2>&1 || true
    move_to_needs_human "change-budget" "$BUDGET_REPORT_PATH"
  elif [ "$BUDGET_EXIT" -ne 0 ]; then
    return 1
  fi

  git commit -m "builder: ${TASK_ID} ${TITLE}" >> "$LOG_PATH" 2>&1

  if [ "$CHANGE_RISK_EXIT" -eq 3 ]; then
    move_to_needs_human "change-risk" "$CHANGE_RISK_REPORT_PATH"
  fi
}

if ! "$ROOT/scripts/create_worktree.sh" "$REPO_MAIN" "$WORKTREE_PATH" "$BRANCH" "$BASE_BRANCH" "$SOURCE_REF" >> "$LOG_PATH" 2>&1; then
  fail_task
fi

cd "$WORKTREE_PATH"

start_task_watchdog

log_git_diff_context

if ! python3 "$ROOT/manager/task_schema.py" render-prompt "$RUNNING_FILE" "$PROMPT_PATH" "$PROMPT_TMP" >> "$LOG_PATH" 2>&1
then
  fail_task
fi

set +e
CODEX_HOME="$CODEX_HOME_PATH" codex exec --full-auto --skip-git-repo-check "$(cat "$PROMPT_TMP")" > "$LOG_PATH" 2>&1
EXIT_CODE=$?
set -e

if [ "$EXIT_CODE" -eq 0 ]; then
  if ! commit_builder_changes; then
    fail_task
  fi
  jq --arg lifecycle "$DONE_LIFECYCLE" '.status = "done" | .lifecycle_state = $lifecycle' "$RUNNING_FILE" > "$RUNNING_FILE.tmp" && mv "$RUNNING_FILE.tmp" "$RUNNING_FILE"
  write_result "$RUNNING_FILE"
  finish_task_watchdog "done" "done"
  mv "$RUNNING_FILE" "$QUEUE_DONE/$BASENAME"
  exit 0
fi

if grep -qi '429\|rate limit\|too many requests' "$LOG_PATH" && [ "$RETRY_COUNT" -lt 4 ]; then
  NEW_BRANCH="$(next_retry_branch "$BRANCH" "$RETRY_COUNT")"
  case "$RETRY_COUNT" in
    0) SLEEP_SECS=30 ;;
    1) SLEEP_SECS=60 ;;
    2) SLEEP_SECS=120 ;;
    *) SLEEP_SECS=240 ;;
  esac
  jq --arg branch "$NEW_BRANCH" '.retry_count = (.retry_count + 1) | .branch = $branch | .status = "pending" | .lifecycle_state = "routed"' "$RUNNING_FILE" > "$RUNNING_FILE.tmp"
  mv "$RUNNING_FILE.tmp" "$RUNNING_FILE"
  printf 'retry scheduled with new branch: %s\n' "$NEW_BRANCH" >> "$LOG_PATH"
  finish_task_watchdog "pending" "pending"
  sleep "$SLEEP_SECS"
  mv "$RUNNING_FILE" "$QUEUE_PENDING/$BASENAME"
  exit 0
fi

mv "$RUNNING_FILE" "$QUEUE_FAILED/$BASENAME"
exit 1
