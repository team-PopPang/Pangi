#!/usr/bin/env bash
#
# Pangi Slack bot 배포 스크립트입니다.
# 로컬 pangi/.env를 포함해 서버로 전송합니다.

set -Eeuo pipefail

SSH_HOST=${SSH_HOST:-poppang-server}
SERVER_DIR=${SERVER_DIR:-/home/poppang/admin}
APP_NAME=${APP_NAME:-pangi}
APP_PORT=${APP_PORT:-4100}
INSTALL_REQUIREMENTS=${INSTALL_REQUIREMENTS:-1}
RESTART_BOT=${RESTART_BOT:-1}

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

Environment overrides:
  SSH_HOST=poppang-server $0
  APP_PORT=4100 $0
  INSTALL_REQUIREMENTS=0 $0
  RESTART_BOT=0 $0
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
[[ -f ".env" ]] || die "env file not found: .env"

command -v ssh >/dev/null 2>&1 || die "ssh command not found"
command -v tar >/dev/null 2>&1 || die "tar command not found"

REMOTE_BASE="${SERVER_DIR%/}"
REMOTE_TARGET_DIR="$REMOTE_BASE/$APP_NAME"
REMOTE_BASE_Q="$(shell_quote "$REMOTE_BASE")"
REMOTE_TARGET_DIR_Q="$(shell_quote "$REMOTE_TARGET_DIR")"

REMOTE_COMMAND="
set -Eeuo pipefail
mkdir -p $REMOTE_BASE_Q
rm -rf $REMOTE_TARGET_DIR_Q
mkdir -p $REMOTE_TARGET_DIR_Q
tar -xzf - -C $REMOTE_TARGET_DIR_Q

rm -rf \"$REMOTE_TARGET_DIR/__pycache__\"
find \"$REMOTE_TARGET_DIR\" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

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
echo "  include env: .env"
echo ""
echo "Uploading..."

COPYFILE_DISABLE=1 tar --no-xattrs -C "$SCRIPT_DIR" \
  --exclude='.DS_Store' \
  --exclude='*/.DS_Store' \
  --exclude='__pycache__' \
  --exclude='*/__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*/.pytest_cache' \
  --exclude='.venv' \
  --exclude='.data' \
  --exclude='*.egg-info' \
  --exclude='*.pyc' \
  -czf - . | ssh "$SSH_HOST" "bash -lc $(shell_quote "$REMOTE_COMMAND")"

echo ""
echo "Done."
