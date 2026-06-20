#!/usr/bin/env zsh
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo "pangi/.env already exists."
  echo "Edit it and make sure these values are not empty:"
  echo "  SLACK_SIGNING_SECRET"
  echo "  SLACK_BOT_TOKEN"
  echo "  SLACK_ALLOWED_USER_IDS"
  echo "  SLACK_ALLOWED_CHANNEL_IDS"
  exit 0
fi

cp .env.example .env
echo "Created pangi/.env from .env.example."
echo "For /health only, the dummy Slack values in .env.example are enough."
