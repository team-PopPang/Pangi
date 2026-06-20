#!/usr/bin/env bash
#
# PopPang Slack bot 배포 스크립트입니다.
# 로컬 poppangbot/.env를 포함해 서버로 전송합니다.

set -Eeuo pipefail

SSH_HOST=${SSH_HOST:-poppang-server}
SERVER_DIR=${SERVER_DIR:-/home/poppang/admin}
BOT_DIR=${BOT_DIR:-poppangbot}
BOT_PORT=${BOT_PORT:-4100}
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
  BOT_DIR=$BOT_DIR
  BOT_PORT=$BOT_PORT
  INSTALL_REQUIREMENTS=$INSTALL_REQUIREMENTS
  RESTART_BOT=$RESTART_BOT

Environment overrides:
  SSH_HOST=poppang-server $0
  BOT_PORT=4100 $0
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

[[ -d "$BOT_DIR" ]] || die "directory not found: $BOT_DIR"
[[ -f "$BOT_DIR/app.py" ]] || die "FastAPI app not found: $BOT_DIR/app.py"
[[ -f "$BOT_DIR/requirements.txt" ]] || die "requirements not found: $BOT_DIR/requirements.txt"
[[ -f "$BOT_DIR/.env" ]] || die "env file not found: $BOT_DIR/.env"

command -v ssh >/dev/null 2>&1 || die "ssh command not found"
command -v tar >/dev/null 2>&1 || die "tar command not found"

REMOTE_BASE="${SERVER_DIR%/}"
REMOTE_TARGET_DIR="$REMOTE_BASE/$BOT_DIR"
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
fi

if [[ \"$RESTART_BOT\" == \"1\" ]]; then
  bot_pids() {
    ps -eo pid=,args= | awk -v port=\"$BOT_PORT\" -v target=\"$REMOTE_TARGET_DIR\" '
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
  nohup \"\$PYTHON_BIN\" -m uvicorn app:app --host 0.0.0.0 --port \"$BOT_PORT\" > \"$REMOTE_TARGET_DIR/nohup.out\" 2>&1 </dev/null &
  echo \"PopPang bot restarted: $REMOTE_TARGET_DIR/app.py (port $BOT_PORT)\"
fi

echo \"Remote deploy completed: $REMOTE_TARGET_DIR\"
"

echo "Deploy target"
echo "  host: $SSH_HOST"
echo "  local: $SCRIPT_DIR/$BOT_DIR"
echo "  remote: $REMOTE_TARGET_DIR"
echo "  bot port: $BOT_PORT"
echo "  install requirements: $INSTALL_REQUIREMENTS"
echo "  restart bot: $RESTART_BOT"
echo "  include env: $BOT_DIR/.env"
echo ""
echo "Uploading..."

COPYFILE_DISABLE=1 tar --no-xattrs -C "$BOT_DIR" \
  --exclude='.DS_Store' \
  --exclude='*/.DS_Store' \
  --exclude='__pycache__' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  -czf - . | ssh "$SSH_HOST" "bash -lc $(shell_quote "$REMOTE_COMMAND")"

echo ""
echo "Done."
