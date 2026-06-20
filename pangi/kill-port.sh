#!/usr/bin/env zsh
set -euo pipefail

if (( $# != 1 )); then
  echo "Usage: $0 <port>"
  echo "Example: $0 8000"
  exit 1
fi

PORT="$1"

if ! [[ "${PORT}" =~ '^[0-9]+$' ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Port must be a number between 1 and 65535."
  exit 1
fi

find_pids() {
  local port="$1"
  local output

  output="$(lsof -nP -iTCP:${port} -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -z "${output}" ]]; then
    output="$(lsof -nP -i:${port} -t 2>/dev/null || true)"
  fi

  if [[ -n "${output}" ]]; then
    print -r -- "${output}"
  fi
}

pid_output="$(find_pids "${PORT}")"

if [[ -z "${pid_output}" ]]; then
  echo "No process is listening on port ${PORT}."
  exit 0
fi

pids=("${(@f)pid_output}")

echo "Stopping process(es) on port ${PORT}: ${pids[*]}"
kill "${pids[@]}"

sleep 1

remaining_output="$(find_pids "${PORT}")"
if [[ -n "${remaining_output}" ]]; then
  remaining=("${(@f)remaining_output}")
  echo "Force stopping remaining process(es): ${remaining[*]}"
  kill -9 "${remaining[@]}"
fi

echo "Port ${PORT} is clear."
