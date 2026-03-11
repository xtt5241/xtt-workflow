#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/xtt-workflow"
ROLES=(builder reviewer verifier planner)

for role in "${ROLES[@]}"; do
  target_dir="$ROOT/codex-homes/$role/skills"
  mkdir -p "$target_dir"
  for skill_dir in "$ROOT"/skills/*; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    ln -sfn "../../../skills/$skill_name" "$target_dir/$skill_name"
  done
  echo "linked skills for $role -> $target_dir" >&2
done
