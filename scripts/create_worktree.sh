#!/usr/bin/env bash
set -euo pipefail

REPO_MAIN="$1"
WORKTREE_PATH="$2"
BRANCH_NAME="$3"
BASE_BRANCH="${4:-main}"
SOURCE_REF="${5:-origin/$BASE_BRANCH}"

cd "$REPO_MAIN"
git fetch origin
git worktree prune

git worktree remove "$WORKTREE_PATH" --force 2>/dev/null || true
rm -rf "$WORKTREE_PATH"

git rev-parse --verify "$SOURCE_REF" >/dev/null 2>&1
if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
  git branch -D "$BRANCH_NAME"
fi
git worktree add -b "$BRANCH_NAME" "$WORKTREE_PATH" "$SOURCE_REF"
