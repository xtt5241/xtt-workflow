#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/xtt-workflow"
REPO="${1:?usage: onboard_repo_profile.sh <repo>}"
REPO_PATH="$ROOT/workspace/$REPO"
OUTPUT_PATH="$ROOT/config/repos/$REPO.json"

if [ ! -d "$REPO_PATH/.git" ]; then
  echo "repo not found under workspace/: $REPO_PATH" >&2
  exit 2
fi

python3 "$ROOT/skills/repo-understand/scripts/suggest_repo_profile.py" \
  "$REPO_PATH" \
  --repo-name "$REPO" \
  --write-profile "$OUTPUT_PATH"

echo "seeded repo profile: $OUTPUT_PATH" >&2
