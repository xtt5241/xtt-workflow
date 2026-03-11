#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME/xtt-workflow"/{workspace,manager/{workers,prompts,queue/{pending,running,done,failed},logs,state},scripts,config,codex-homes/{planner,builder,reviewer,verifier},backups}

sudo apt update
sudo apt install -y git curl wget unzip jq tmux python3 python3-venv python3-pip build-essential ripgrep fd-find rsync nodejs npm

if ! command -v codex >/dev/null 2>&1; then
  sudo npm install -g @openai/codex
fi

echo "bootstrap done"
