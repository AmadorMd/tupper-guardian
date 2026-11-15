import pytest

from guardian import messages, notify, telegram

TUPPER = {"item_id": "T#abc123", "tupper_id": "abc123", "food": "caldo", "days": 3}


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram, "call", lambda method, payload: calls.append((method, payload)))
    return calls


def test_plain_message_is_sent_without_buttons(sent):
    notify.handler({"chat_id": 111, "kind": "stored", "data": TUPPER})

    assert len(sent) == 1
    method, payload = sent[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == 111
    assert "caldo" in payload["text"] and "3 días" in payload["text"]
    assert "reply_markup" not in payload


def test_ask_saves_the_token_before_sending_the_buttons(sent, tables, monkeypatch):
    tables.put_item(Item={"chat_id": "111", "item_id": "T#abc123", "status": "ACTIVE"})
    token_at_send_time = []
    original = telegram.call

    def spy(method, payload):
        item = tables.get_item(Key={"chat_id": "111", "item_id": "T#abc123"})["Item"]
        token_at_send_time.append(item.get("task_token"))
        return original(method, payload)

    monkeypatch.setattr(telegram, "call", spy)

    notify.handler({"chat_id": 111, "kind": "ask", "data": TUPPER, "task_token": "TOKEN"})

    assert token_at_send_time == ["TOKEN"]
    buttons = sent[0][1]["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in buttons] == ["e|abc123", "t|abc123", "s|abc123"]


def test_ask_for_a_deleted_tupper_does_not_create_a_phantom_item(sent, tables):
    notify.handler({"chat_id": 111, "kind": "ask", "data": TUPPER, "task_token": "TOKEN"})

    assert "Item" not in tables.get_item(Key={"chat_id": "111", "item_id": "T#abc123"})
    assert len(sent) == 1


def test_button_press_is_acknowledged_before_the_reply(sent):
    notify.handler(
        {
            "chat_id": 111,
            "kind": "resolved",
            "data": {"action": "eaten", "rescued_count": 2},
            "callback_query_id": "cb-1",
        }
    )

    assert sent[0] == ("answerCallbackQuery", {"callback_query_id": "cb-1"})
    assert sent[1][0] == "sendMessage"
    assert "2 tuppers rescatados" in sent[1][1]["text"]


def test_empty_callback_id_is_not_acknowledged(sent):
    notify.handler({"chat_id": 111, "kind": "help", "data": {}, "callback_query_id": ""})

    assert [method for method, _ in sent] == ["sendMessage"]


def test_voice_failure_does_not_fail_the_notification(sent, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "true")

    def broken(_speech):
        raise RuntimeError("polly down")

    monkeypatch.setattr(notify, "_synthesize", broken)

    assert notify.handler({"chat_id": 111, "kind": "reminder", "data": TUPPER}) == {"ok": True}
    assert len(sent) == 1


def test_voice_note_follows_the_text_when_enabled(sent, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setattr(notify, "_synthesize", lambda speech: speech.encode())
    voices = []
    monkeypatch.setattr(telegram, "send_voice", lambda chat, audio: voices.append((chat, audio)))

    notify.handler({"chat_id": 111, "kind": "reminder", "data": TUPPER})

    assert voices == [(111, "Tu caldo vence mañana. Aprovéchalo hoy.".encode())]


@pytest.mark.parametrize(
    ("kind", "data", "expected"),
    [
        ("stored", {"food": "guacamole", "days": 1, "default_used": True}, "1 día"),
        ("stored", {"food": "x", "days": 2, "default_used": True}, "tiempo corto por seguridad"),
        ("consumed", {"found": True, "food": "caldo", "rescued_count": 1}, "1 tupper rescatado"),
        ("consumed", {"found": False}, "No encontré"),
        ("list", {"items": [], "rescued_count": 0}, "No tienes tuppers"),
        ("list", {"items": [{"food": "caldo", "days_left": 0}]}, "caldo: ya venció"),
        ("list", {"items": [{"food": "caldo", "days_left": 2}]}, "caldo: vence en 2 días"),
        ("invite", {"code": "XYZ"}, "https://t.me/guardian_test_bot?start=care_XYZ"),
        ("link", {"linked": True, "owner_name": "Lupita"}, "Lupita"),
        ("link", {"linked": False}, "ya no es válido"),
        ("resolved", {"action": "already"}, "ya estaba resuelto"),
        ("resolved", {"action": "snooze"}, "mañana"),
        ("expired", {"food": "caldo"}, "vencido"),
        ("escalation", {"food": "caldo", "owner_name": "Lupita"}, "Lupita tiene caldo vencido"),
        ("escalation", {"food": "caldo", "owner_name": ""}, "Tu familiar tiene caldo"),
        ("voice_unsupported", {}, "texto y fotos"),
        ("something_new", {}, "No te entendí"),
    ],
)
def test_message_catalog(kind, data, expected):
    assert expected in messages.build(kind, data).text


def test_only_proactive_messages_are_spoken():
    spoken = {
        kind
        for kind, data in [
            ("stored", TUPPER),
            ("reminder", TUPPER),
            ("ask", TUPPER),
            ("list", {}),
            ("help", {}),
            ("resolved", {"action": "eaten"}),
        ]
        if messages.build(kind, data).speech
    }

    assert spoken == {"stored", "reminder", "ask"}
