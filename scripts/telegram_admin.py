#!/usr/bin/env python3
"""Telegram bot administration used by deploy.sh. Standard library only.

Usage:
  telegram_admin.py username                 Print the bot username.
  telegram_admin.py set-webhook <base-url>   Point the bot at <base-url>/<secret>.
  telegram_admin.py info                     Show webhook status (secret redacted).

The bot token is read from SSM (BOT_TOKEN_PARAM) through the AWS CLI and the
webhook secret from the local .webhook-secret file. Neither is ever printed.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_PARAM = os.environ.get("BOT_TOKEN_PARAM", "/tupper-guardian/bot-token")


def token():
    result = subprocess.run(
        ["aws", "ssm", "get-parameter", "--name", TOKEN_PARAM, "--with-decryption",
         "--query", "Parameter.Value", "--output", "text"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def call(method, payload=None):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token()}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        sys.exit(f"{method} failed with HTTP {error.code}: {detail}")
    if not body.get("ok"):
        sys.exit(f"{method} rejected: {body.get('description')}")
    return body["result"]


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "username":
        print(call("getMe")["username"])
    elif command == "set-webhook" and len(argv) == 3:
        secret = (ROOT / ".webhook-secret").read_text("utf-8").strip()
        call("setWebhook", {
            "url": f"{argv[2].rstrip('/')}/{secret}",
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        })
        print("Webhook registered.")
    elif command == "info":
        info = call("getWebhookInfo")
        if info.get("url"):
            info["url"] = info["url"].rsplit("/", 1)[0] + "/<secret>"
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
