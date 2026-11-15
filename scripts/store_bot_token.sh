#!/usr/bin/env bash
# Store the Telegram bot token (from @BotFather) as an SSM SecureString.
# The token is read without echo and is never written to disk.
set -euo pipefail

PARAM="${BOT_TOKEN_PARAM:-/tupper-guardian/bot-token}"

read -r -s -p "Paste the bot token from @BotFather: " TOKEN
echo
if [[ ! "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
  echo "That does not look like a bot token (expected 123456:ABC...)." >&2
  exit 1
fi

aws ssm put-parameter --name "$PARAM" --type SecureString --value "$TOKEN" --overwrite >/dev/null
echo "Token stored in SSM parameter $PARAM."
