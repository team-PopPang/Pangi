#!/usr/bin/env bash
#
# Pangi 운영용 code-only 배포 스크립트입니다.
# 원격 .env, .data, nohup.out 같은 런타임 상태 파일은 보존합니다.

set -Eeuo pipefail

SSH_HOST=${SSH_HOST:-poppang-server}
SERVER_DIR=${SERVER_DIR:-/home/poppang/admin}
APP_NAME=${APP_NAME:-pangi}
APP_PORT=${APP_PORT:-4100}
INSTALL_REQUIREMENTS=${INSTALL_REQUIREMENTS:-1}
RESTART_BOT=${RESTART_BOT:-1}
SYNC_TESTS=${SYNC_TESTS:-0}
DRY_RUN=${DRY_RUN:-0}
REQUIRE_REMOTE_ENV=${REQUIRE_REMOTE_ENV:-1}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
  cat <<EOF
Usage:
  $0

Deploy config:
  SSH_HOST=$SSH_HOST
  SERVER_DIR=$SERVER_DIR
  APP_NAME=$APP_NAME
  APP_PORT=$APP_PORT
  INSTALL_REQUIREMENTS=$INSTALL_REQUIREMENTS
  RESTART_BOT=$RESTART_BOT
  SYNC_TESTS=$SYNC_TESTS
  DRY_RUN=$DRY_RUN
  REQUIRE_REMOTE_ENV=$REQUIRE_REMOTE_ENV

Environment overrides:
  SSH_HOST=poppang-server $0
  APP_PORT=4100 $0
  INSTALL_REQUIREMENTS=0 $0
  RESTART_BOT=0 $0
  SYNC_TESTS=1 $0
  DRY_RUN=1 $0
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

shell_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

[[ $# -eq 0 ]] || {
  usage >&2
  exit 1
}

[[ -f "src/pangi/app.py" ]] || die "FastAPI app not found: src/pangi/app.py"
[[ -f "requirements.txt" ]] || die "requirements not found: requirements.txt"
[[ -f "pyproject.toml" ]] || die "pyproject.toml not found"
[[ -f "README.md" ]] || die "README.md not found"

command -v ssh >/dev/null 2>&1 || die "ssh command not found"
command -v rsync >/dev/null 2>&1 || die "rsync command not found"

REMOTE_BASE="${SERVER_DIR%/}"
REMOTE_TARGET_DIR="$REMOTE_BASE/$APP_NAME"
REMOTE_BASE_Q="$(shell_quote "$REMOTE_BASE")"
REMOTE_TARGET_DIR_Q="$(shell_quote "$REMOTE_TARGET_DIR")"

RSYNC_FLAGS=(-az)
if [[ "$DRY_RUN" == "1" ]]; then
  RSYNC_FLAGS+=(--dry-run)
fi

run_rsync() {
  local source_path="$1"
  local target_path="$2"
  shift 2
  rsync "${RSYNC_FLAGS[@]}" "$@" "$source_path" "$target_path"
}

REMOTE_PREPARE_COMMAND="
set -Eeuo pipefail
mkdir -p $REMOTE_TARGET_DIR_Q
"

REMOTE_POST_COMMAND="
set -Eeuo pipefail

if [[ \"$REQUIRE_REMOTE_ENV\" == \"1\" && ! -f \"$REMOTE_TARGET_DIR/.env\" ]]; then
  echo \"ERROR: remote env file not found: $REMOTE_TARGET_DIR/.env\" >&2
  exit 1
fi

find \"$REMOTE_TARGET_DIR/src\" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

PYTHON_BIN=\"$REMOTE_BASE/.venv/bin/python\"
if [[ ! -x \"\$PYTHON_BIN\" ]]; then
  PYTHON_BIN=\"\$(command -v python3 || command -v python)\"
fi

if [[ \"$INSTALL_REQUIREMENTS\" == \"1\" ]]; then
  \"\$PYTHON_BIN\" -m pip install -r \"$REMOTE_TARGET_DIR/requirements.txt\"
  \"\$PYTHON_BIN\" -m pip install -e \"$REMOTE_TARGET_DIR\"
fi

if [[ \"$RESTART_BOT\" == \"1\" ]]; then
  bot_pids() {
    ps -eo pid=,args= | awk -v port=\"$APP_PORT\" -v target=\"$REMOTE_TARGET_DIR\" '
      index(\$0, \"uvicorn\") &&
      (index(\$0, target) || \$0 ~ \"--port[ =]?\" port) &&
      \$0 !~ /bash -lc/ &&
      \$0 !~ /awk/ {
        print \$1
      }
    '
  }

  BOT_PIDS=\"\$(bot_pids)\"
  if [[ -n \"\$BOT_PIDS\" ]]; then
    kill \$BOT_PIDS 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      if [[ -z \"\$(bot_pids)\" ]]; then
        break
      fi
      sleep 1
    done
    kill -9 \$BOT_PIDS 2>/dev/null || true
  fi

  cd \"$REMOTE_TARGET_DIR\"
  nohup \"\$PYTHON_BIN\" -m uvicorn pangi.app:app --host 0.0.0.0 --port \"$APP_PORT\" > \"$REMOTE_TARGET_DIR/nohup.out\" 2>&1 </dev/null &
  echo \"Pangi bot restarted: $REMOTE_TARGET_DIR/src/pangi/app.py (port $APP_PORT)\"
fi

echo \"Remote deploy completed: $REMOTE_TARGET_DIR\"
"

echo "Deploy target"
echo "  host: $SSH_HOST"
echo "  local: $SCRIPT_DIR"
echo "  remote: $REMOTE_TARGET_DIR"
echo "  app port: $APP_PORT"
echo "  install requirements: $INSTALL_REQUIREMENTS"
echo "  restart bot: $RESTART_BOT"
echo "  sync tests: $SYNC_TESTS"
echo "  dry run: $DRY_RUN"
echo "  preserve remote state: .env, .data, nohup.out"
echo ""

ssh "$SSH_HOST" "bash -lc $(shell_quote "$REMOTE_PREPARE_COMMAND")"

echo "Syncing runtime files..."
run_rsync "README.md" "$SSH_HOST:$REMOTE_TARGET_DIR/README.md"
run_rsync "pyproject.toml" "$SSH_HOST:$REMOTE_TARGET_DIR/pyproject.toml"
run_rsync "requirements.txt" "$SSH_HOST:$REMOTE_TARGET_DIR/requirements.txt"
run_rsync "src/" "$SSH_HOST:$REMOTE_TARGET_DIR/src/" \
  --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc'

if [[ "$SYNC_TESTS" == "1" ]]; then
  echo "Syncing tests..."
  run_rsync "tests/" "$SSH_HOST:$REMOTE_TARGET_DIR/tests/" \
    --delete \
    --exclude='__pycache__/' \
    --exclude='*.pyc'
fi

echo "Running remote post-deploy steps..."
ssh "$SSH_HOST" "bash -lc $(shell_quote "$REMOTE_POST_COMMAND")"

echo ""
echo "Done."
