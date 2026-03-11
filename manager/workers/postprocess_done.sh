#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/xtt-workflow"
DONE="$ROOT/manager/queue/done"
PENDING="$ROOT/manager/queue/pending"
READY_PUSH="$ROOT/manager/queue/ready-to-push"
READY_PR="$ROOT/manager/queue/ready-to-pr"
NEEDS_HUMAN="$ROOT/manager/queue/needs-human"
LOG_DIR="$ROOT/manager/logs"
RESULTS_DIR="$ROOT/manager/results"
STATE="$ROOT/manager/state/postprocess.db"

mkdir -p "$ROOT/manager/state" "$DONE" "$PENDING" "$READY_PUSH" "$READY_PR" "$NEEDS_HUMAN" "$LOG_DIR" "$RESULTS_DIR"
touch "$STATE"

write_result() {
  local task_file="$1"
  local task_id
  task_id="$(jq -r '.id' "$task_file")"
  python3 "$ROOT/manager/result_writer.py" write "$task_file" "$LOG_DIR/${task_id}.log" "$RESULTS_DIR/${task_id}.json" >/dev/null 2>&1 || true
}

worktree_name() {
  local repo="$1"
  local stage="$2"
  printf '%s-wt-%s' "$repo" "$stage"
}

done_lifecycle_state() {
  local type="$1"
  case "$type" in
    build) printf 'build-done' ;;
    review) printf 'review-done' ;;
    verify) printf 'verify-done' ;;
    *) printf 'delivered' ;;
  esac
}

queue_ready_gate() {
  local source_file="$1"
  local gate_dir="$2"
  local gate_status="$3"
  local id repo base_branch branch gate_file

  id="$(jq -r '.id' "$source_file")"
  repo="$(jq -r '.repo' "$source_file")"
  base_branch="$(jq -r '.base_branch' "$source_file")"
  branch="$(jq -r '.branch' "$source_file")"
  gate_file="$gate_dir/${id}-${gate_status}.json"

  jq --arg gate_status "$gate_status" \
     --arg repo "$repo" \
     --arg base_branch "$base_branch" \
     --arg branch "$branch" \
     '. + {
        "gate_status": $gate_status,
        "gate_repo": $repo,
        "gate_base_branch": $base_branch,
        "gate_branch": $branch,
        "status": $gate_status,
        "lifecycle_state": $gate_status
      }' "$source_file" > "$gate_file"
  write_result "$gate_file"
}

move_done_to_needs_human() {
  local source_file="$1"
  local gate="$2"
  local report_path="$3"
  local target="$NEEDS_HUMAN/$(basename "$source_file")"
  local lifecycle
  lifecycle="$(done_lifecycle_state "$(jq -r '.type' "$source_file")")"

  if [ -f "$report_path" ]; then
    jq --slurpfile report "$report_path" \
       --arg gate "$gate" \
       --arg lifecycle "$lifecycle" \
       --arg updated_at "$(date '+%Y-%m-%d %H:%M:%S')" \
       '.status = "needs-human"
        | .lifecycle_state = $lifecycle
        | .human_gate = $gate
        | .human_reason = (($report[0].summary // "needs human review"))
        | .dod_report = ($report[0] // {})
        | .human_updated_at = $updated_at' "$source_file" > "$target"
  else
    jq --arg gate "$gate" \
       --arg lifecycle "$lifecycle" \
       --arg updated_at "$(date '+%Y-%m-%d %H:%M:%S')" \
       '.status = "needs-human"
        | .lifecycle_state = $lifecycle
        | .human_gate = $gate
        | .human_reason = "needs human review"
        | .human_updated_at = $updated_at' "$source_file" > "$target"
  fi
  write_result "$target"
  rm -f "$source_file"
}

for f in "$DONE"/*.json; do
  [ -e "$f" ] || continue
  id="$(jq -r '.id' "$f")"
  grep -q "^$id$" "$STATE" && continue
  type="$(jq -r '.type' "$f")"
  title="$(jq -r '.title' "$f")"
  repo="$(jq -r '.repo' "$f")"
  base_branch="$(jq -r '.base_branch' "$f")"
  branch="$(jq -r '.branch' "$f")"
  task_kind="$(jq -r '.task_kind // "feature"' "$f")"
  dod_summary="$(jq -r '.dod_summary // empty' "$f")"
  repo_profile="$(jq -c '.repo_profile // {}' "$f")"
  repo_profile_summary="$(jq -r '.repo_profile_summary // empty' "$f")"
  backlog_item_id="$(jq -r '.backlog_item_id // empty' "$f")"
  backlog_item_title="$(jq -r '.backlog_item_title // empty' "$f")"
  risk_level="$(jq -r '.risk_level // "medium"' "$f")"
  evidence_required="$(jq -c '.evidence_required // []' "$f")"
  change_budget="$(jq -c '.change_budget // {"max_files":0,"max_lines":0}' "$f")"
  allow_push="$(jq -r '.allow_push // false' "$f")"
  allow_pr="$(jq -r '.allow_pr // false' "$f")"
  allowed_paths="$(jq -c '.allowed_paths // []' "$f")"
  forbidden_paths="$(jq -c '.forbidden_paths // []' "$f")"
  if [ "$type" = "build" ]; then
    review_id="${id}-review"
    cat > "$PENDING/${review_id}.json" <<JSON
{
  "id": "$review_id",
  "type": "review",
  "task_kind": "$task_kind",
  "title": "Review $branch",
  "repo": "$repo",
  "base_branch": "$base_branch",
  "repo_profile": $repo_profile,
  "repo_profile_summary": $(jq -Rn --arg value "$repo_profile_summary" '$value'),
  "dod_summary": $(jq -Rn --arg value "$dod_summary" '$value'),
  "source_ref": "$branch",
  "goal": "Review $branch against origin/$base_branch",
  "acceptance": [
    "Inspect the real diff against origin/$base_branch...HEAD",
    "Only output issues, do not fix code"
  ],
  "risk_level": "$risk_level",
  "evidence_required": $evidence_required,
  "change_budget": $change_budget,
  "allow_auto_commit": false,
  "allow_push": $allow_push,
  "allow_pr": $allow_pr,
  "allowed_paths": $allowed_paths,
  "forbidden_paths": $forbidden_paths,
  "backlog_item_id": "$backlog_item_id",
  "backlog_item_title": "$backlog_item_title",
  "worktree": "$(worktree_name "$repo" review)",
  "branch": "review/${branch##*/}",
  "prompt_file": "review_prompt.md",
  "role": "reviewer",
  "status": "pending",
  "lifecycle_state": "routed",
  "retry_count": 0,
  "depends_on": ["$id"]
}
JSON
    python3 "$ROOT/manager/task_schema.py" normalize "$PENDING/${review_id}.json" --in-place >/dev/null
    python3 "$ROOT/manager/test_strategy.py" apply "$PENDING/${review_id}.json" --in-place >/dev/null
  elif [ "$type" = "review" ]; then
    verify_id="${id}-verify"
    cat > "$PENDING/${verify_id}.json" <<JSON
{
  "id": "$verify_id",
  "type": "verify",
  "task_kind": "$task_kind",
  "title": "Verify $branch",
  "repo": "$repo",
  "base_branch": "$base_branch",
  "repo_profile": $repo_profile,
  "repo_profile_summary": $(jq -Rn --arg value "$repo_profile_summary" '$value'),
  "dod_summary": $(jq -Rn --arg value "$dod_summary" '$value'),
  "source_ref": "$branch",
  "goal": "Verify $branch against origin/$base_branch",
  "acceptance": [
    "Verify the real diff against origin/$base_branch...HEAD",
    "Confirm whether the change is truly usable"
  ],
  "risk_level": "$risk_level",
  "evidence_required": $evidence_required,
  "change_budget": $change_budget,
  "allow_auto_commit": false,
  "allow_push": $allow_push,
  "allow_pr": $allow_pr,
  "allowed_paths": $allowed_paths,
  "forbidden_paths": $forbidden_paths,
  "backlog_item_id": "$backlog_item_id",
  "backlog_item_title": "$backlog_item_title",
  "worktree": "$(worktree_name "$repo" verify)",
  "branch": "verify/${branch##*/}",
  "prompt_file": "verify_prompt.md",
  "role": "verifier",
  "status": "pending",
  "lifecycle_state": "routed",
  "retry_count": 0,
  "depends_on": ["$id"]
}
JSON
    python3 "$ROOT/manager/task_schema.py" normalize "$PENDING/${verify_id}.json" --in-place >/dev/null
    python3 "$ROOT/manager/test_strategy.py" apply "$PENDING/${verify_id}.json" --in-place >/dev/null
  elif [ "$type" = "verify" ]; then
    if [ "$(jq -r '.human_override // false' "$f")" != "true" ]; then
      DOD_REPORT="$ROOT/manager/state/${id}.dod.json"
      set +e
      python3 "$ROOT/manager/dod.py" check-verify "$f" "$LOG_DIR/${id}.log" "$DOD_REPORT"
      DOD_EXIT=$?
      set -e
      if [ "$DOD_EXIT" -ne 0 ]; then
        move_done_to_needs_human "$f" "dod" "$DOD_REPORT"
        continue
      fi
    fi
    if [ "$allow_push" = "true" ]; then
      queue_ready_gate "$f" "$READY_PUSH" "ready-to-push"
    else
      queue_ready_gate "$f" "$READY_PR" "ready-to-pr"
    fi
  fi
  echo "$id" >> "$STATE"
done
