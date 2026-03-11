#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/xtt-workflow"
CODEX_ROOT="$ROOT/codex-homes"

BASE_URL="https://vpsairobot.com"
BUILDER_POLICY_VERSION="builder-policy-v1"
BUILDER_KEY="sk-e10f6425b2b511d71974158bb26b68d852ee85431ce75a55bfc0e9f83ef40fa4"
REVIEWER_KEY="sk-248e9ac6c0926ddff79b09e808bbbc95b5a4e842c13356df5a7d774e41569649"
VERIFIER_KEY="sk-741bc350c768a881440ffe1f147549fd73ca9ed7e08afc7f1b323f4c52f864f3"
PLANNER_KEY="sk-ac8b5992d256c923c3d2079a6dde15b62fa81b493cc33354ef0dc43ec883149a"

usage() {
  cat <<'EOF'
Usage:
  setup_relay_keys.sh [--restart-tmux]

Rules:
  - builder   -> fixed profile -> relay-a -> BUILDER_KEY
  - reviewer  -> relay-b -> REVIEWER_KEY
  - verifier  -> relay-c -> VERIFIER_KEY
  - planner   -> relay-b -> PLANNER_KEY (fallback REVIEWER_KEY)

Examples:
  1. Edit this file and fill BASE_URL / BUILDER_KEY / REVIEWER_KEY / VERIFIER_KEY
  2. Run ./setup_relay_keys.sh
  ./setup_relay_keys.sh --restart-tmux
EOF
}

RESTART_TMUX=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --restart-tmux)
      RESTART_TMUX=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

validate_value() {
  local name="$1"
  local value="$2"
  local placeholder="$3"

  if [ -z "$value" ] || [ "$value" = "$placeholder" ]; then
    echo "Please edit $0 and set $name first." >&2
    exit 1
  fi
}

write_config() {
  local role="$1"
  local provider="$2"
  local reasoning="$3"
  local verbosity="$4"
  local path="$CODEX_ROOT/$role/config.toml"

  cat > "$path" <<EOF
model_provider = "$provider"
model = "gpt-5.4"
model_reasoning_effort = "$reasoning"
model_verbosity = "$verbosity"
network_access = "enabled"
disable_response_storage = true

[model_providers.$provider]
name = "$provider"
base_url = "$BASE_URL"
wire_api = "responses"
requires_openai_auth = true
EOF
}

write_builder_config() {
  local path="$CODEX_ROOT/builder/config.toml"

  cat > "$path" <<EOF
# managed_by = "scripts/setup_relay_keys.sh"
# policy = "$BUILDER_POLICY_VERSION"
# builder config is intentionally fixed and should not be tuned ad hoc.
model_provider = "relay-a"
model = "gpt-5.4"
model_reasoning_effort = "high"
model_verbosity = "high"
network_access = "enabled"
disable_response_storage = true

[model_providers.relay-a]
name = "relay-a"
base_url = "$BASE_URL"
wire_api = "responses"
requires_openai_auth = true
EOF
}

write_auth() {
  local role="$1"
  local key="$2"
  local path="$CODEX_ROOT/$role/auth.json"

  cat > "$path" <<EOF
{
  "OPENAI_API_KEY": "$key"
}
EOF
  chmod 600 "$path"
}

restart_tmux() {
  local session
  for session in xtt-builder xtt-reviewer xtt-verifier xtt-postprocess xtt-web; do
    tmux has-session -t "$session" 2>/dev/null && tmux kill-session -t "$session" || true
  done

  tmux new-session -d -s xtt-builder "$HOME/xtt-workflow/manager/workers/loop_role.sh builder"
  tmux new-session -d -s xtt-reviewer "$HOME/xtt-workflow/manager/workers/loop_role.sh reviewer"
  tmux new-session -d -s xtt-verifier "$HOME/xtt-workflow/manager/workers/loop_role.sh verifier"
  tmux new-session -d -s xtt-postprocess "$HOME/xtt-workflow/manager/workers/loop_postprocess.sh"
  tmux new-session -d -s xtt-web "bash -lc 'cd $HOME/xtt-workflow/manager && source .venv/bin/activate && python app.py'"
}

mkdir -p \
  "$CODEX_ROOT/planner" \
  "$CODEX_ROOT/builder" \
  "$CODEX_ROOT/reviewer" \
  "$CODEX_ROOT/verifier"

validate_value BASE_URL "$BASE_URL" "https://YOUR-RELAY.example.com"
validate_value BUILDER_KEY "$BUILDER_KEY" "YOUR_BUILDER_KEY_HERE"
validate_value REVIEWER_KEY "$REVIEWER_KEY" "YOUR_REVIEWER_KEY_HERE"
validate_value VERIFIER_KEY "$VERIFIER_KEY" "YOUR_VERIFIER_KEY_HERE"

if [ -z "${PLANNER_KEY:-}" ]; then
  PLANNER_KEY="$REVIEWER_KEY"
fi

write_builder_config
write_config reviewer relay-b medium low
write_config verifier relay-c medium low
write_config planner relay-b medium medium

write_auth builder "$BUILDER_KEY"
write_auth reviewer "$REVIEWER_KEY"
write_auth verifier "$VERIFIER_KEY"
write_auth planner "$PLANNER_KEY"

if [ "$RESTART_TMUX" -eq 1 ]; then
  restart_tmux
fi

cat <<EOF
✅ Relay worker configs updated

Base URL:
  $BASE_URL

Bindings:
  builder  -> fixed profile ($BUILDER_POLICY_VERSION) -> relay-a -> builder key
  reviewer -> relay-b -> reviewer key
  verifier -> relay-c -> verifier key
  planner  -> relay-b -> planner key$( [ "$PLANNER_KEY" = "$REVIEWER_KEY" ] && printf ' (same as reviewer)' )

Files updated:
  $CODEX_ROOT/builder/config.toml
  $CODEX_ROOT/reviewer/config.toml
  $CODEX_ROOT/verifier/config.toml
  $CODEX_ROOT/planner/config.toml
  $CODEX_ROOT/builder/auth.json
  $CODEX_ROOT/reviewer/auth.json
  $CODEX_ROOT/verifier/auth.json
  $CODEX_ROOT/planner/auth.json

Next test:
  CODEX_HOME="$CODEX_ROOT/builder" codex exec --full-auto "读取当前目录，说明项目结构，不要修改代码。"
EOF
