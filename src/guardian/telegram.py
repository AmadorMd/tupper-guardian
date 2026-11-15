"""Minimal Telegram Bot API client built on the standard library.

The bot token is read once per execution environment from SSM Parameter Store
and is never logged: errors report the API method and Telegram's description,
not the request URL (which embeds the token).
"""

import json
import os
import urllib.error
import urllib.request
import uuid

import boto3

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 10

_token = None


class TelegramError(RuntimeError):
    """Raised when the Telegram API rejects or fails a request."""


def bot_token():
    global _token
    if _token is None:
        response = boto3.client("ssm").get_parameter(
            Name=os.environ["BOT_TOKEN_PARAM"], WithDecryption=True
        )
        _token = response["Parameter"]["Value"]
    return _token


def _open(request, method):
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise TelegramError(f"{method} failed with HTTP {error.code}: {detail}") from None
    except urllib.error.URLError as error:
        raise TelegramError(f"{method} failed: {error.reason}") from None


def call(method, payload):
    """Call a Bot API method with a JSON body and return its `result`."""
    request = urllib.request.Request(
        f"{API_BASE}/bot{bot_token()}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    body = json.loads(_open(request, method))
    if not body.get("ok"):
        raise TelegramError(f"{method} rejected: {body.get('description')}")
    return body["result"]


def download_file(file_id):
    """Download a file the bot received (photo, voice) and return its bytes."""
    file_path = call("getFile", {"file_id": file_id})["file_path"]
    request = urllib.request.Request(f"{API_BASE}/file/bot{bot_token()}/{file_path}")
    return _open(request, "downloadFile")


def send_voice(chat_id, audio, filename="voice.mp3", content_type="audio/mpeg"):
    """Send audio bytes as a voice note using a multipart upload."""
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n".encode("utf-8"),
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="voice"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
        audio,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    request = urllib.request.Request(
        f"{API_BASE}/bot{bot_token()}/sendVoice",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    body = json.loads(_open(request, "sendVoice"))
    if not body.get("ok"):
        raise TelegramError(f"sendVoice rejected: {body.get('description')}")
    return body["result"]
