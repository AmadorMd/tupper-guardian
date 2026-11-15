import io
import json
import urllib.error

import boto3
import pytest
from moto import mock_aws

from guardian import telegram

TOKEN = "123456:SECRET-TOKEN"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def api(monkeypatch):
    """Capture outgoing requests and answer them from a queue of bodies or errors."""
    monkeypatch.setattr(telegram, "_token", TOKEN)
    requests, replies = [], []

    def fake_urlopen(request, timeout):
        requests.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply if isinstance(reply, bytes) else json.dumps(reply).encode())

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    return requests, replies


def test_call_posts_json_and_returns_the_result(api):
    requests, replies = api
    replies.append({"ok": True, "result": {"message_id": 7}})

    result = telegram.call("sendMessage", {"chat_id": 111, "text": "hola"})

    assert result == {"message_id": 7}
    assert requests[0].full_url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(requests[0].data) == {"chat_id": 111, "text": "hola"}
    assert requests[0].get_header("Content-type") == "application/json"


def test_call_raises_when_telegram_rejects_the_request(api):
    api[1].append({"ok": False, "description": "chat not found"})

    with pytest.raises(telegram.TelegramError, match="sendMessage rejected: chat not found"):
        telegram.call("sendMessage", {"chat_id": 0})


def test_http_error_reports_status_and_detail_without_the_token(api):
    requests, replies = api
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    replies.append(urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"bot blocked")))

    with pytest.raises(telegram.TelegramError) as raised:
        telegram.call("sendMessage", {"chat_id": 111})

    assert str(raised.value) == "sendMessage failed with HTTP 403: bot blocked"
    assert TOKEN not in str(raised.value)
    assert raised.value.__cause__ is None  # the original error carries the URL


def test_network_error_is_reported_without_the_token(api):
    api[1].append(urllib.error.URLError("timed out"))

    with pytest.raises(telegram.TelegramError) as raised:
        telegram.call("getMe", {})

    assert str(raised.value) == "getMe failed: timed out"
    assert TOKEN not in str(raised.value)


def test_download_file_resolves_the_path_then_fetches_the_bytes(api):
    requests, replies = api
    replies.extend([{"ok": True, "result": {"file_path": "photos/1.jpg"}}, b"JPEG-BYTES"])

    assert telegram.download_file("FILE-ID") == b"JPEG-BYTES"
    assert json.loads(requests[0].data) == {"file_id": "FILE-ID"}
    assert requests[1].full_url == f"https://api.telegram.org/file/bot{TOKEN}/photos/1.jpg"


def test_send_voice_uploads_the_audio_as_multipart(api):
    requests, replies = api
    replies.append({"ok": True, "result": {"message_id": 8}})

    assert telegram.send_voice(111, b"MP3-BYTES") == {"message_id": 8}

    request = requests[0]
    assert request.full_url.endswith("/sendVoice")
    boundary = request.get_header("Content-type").split("boundary=")[1]
    body = request.data
    assert body.endswith(f"\r\n--{boundary}--\r\n".encode())
    assert b'name="chat_id"\r\n\r\n111\r\n' in body
    assert b'name="voice"; filename="voice.mp3"\r\nContent-Type: audio/mpeg\r\n\r\nMP3-BYTES' in body


def test_send_voice_raises_when_telegram_rejects_the_upload(api):
    api[1].append({"ok": False, "description": "file too big"})

    with pytest.raises(telegram.TelegramError, match="sendVoice rejected: file too big"):
        telegram.send_voice(111, b"x")


def test_bot_token_is_read_decrypted_from_ssm_once(monkeypatch):
    monkeypatch.setattr(telegram, "_token", None)
    with mock_aws():
        ssm = boto3.client("ssm", region_name="us-east-1")
        ssm.put_parameter(Name="/test/bot-token", Value=TOKEN, Type="SecureString")
        assert telegram.bot_token() == TOKEN
        ssm.put_parameter(Name="/test/bot-token", Value="other", Type="SecureString", Overwrite=True)
        assert telegram.bot_token() == TOKEN
