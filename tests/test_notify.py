import pytest

from guardian import messages, notify, telegram

TUPPER = {
    "fridge_key": "111",
    "item_id": "T#abc123",
    "tupper_id": "abc123",
    "food": "caldo",
    "days": 3,
}


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


def test_last_chance_ask_drops_the_snooze_button(sent):
    notify.handler({"chat_id": 111, "kind": "ask", "data": {**TUPPER, "last_chance": True}})

    payload = sent[0][1]
    buttons = payload["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in buttons] == ["e|abc123", "t|abc123"]
    assert [row[0]["text"] for row in buttons] == ["🤢 Ya me lo comí", "🗑️ Ya lo tiré"]
    assert "Último aviso" in payload["text"] and "caldo" in payload["text"]


def test_unanswered_item_stays_and_invites_family_only_when_the_fridge_is_not_shared():
    alone = messages.build("unanswered", {"food": "caldo", "others": []}).text
    shared = messages.build("unanswered", {"food": "caldo", "others": ["222"]}).text

    assert "lo dejé en el refri" in alone and "/refri" in alone
    assert "/familiar" in alone and "compartirle el refri" in alone
    assert "/familiar" not in shared and "lo dejé en el refri" in shared


def test_question_asked_by_a_relative_reaches_the_token_of_the_shared_fridge(sent, tables):
    tables.put_item(Item={"chat_id": "111", "item_id": "T#abc123", "status": "ACTIVE"})

    notify.handler({"chat_id": 222, "kind": "ask", "data": TUPPER, "task_token": "TOKEN"})

    assert tables.get_item(Key={"chat_id": "111", "item_id": "T#abc123"})["Item"][
        "task_token"
    ] == "TOKEN"
    assert sent[0][1]["chat_id"] == 222


def test_one_notification_can_reach_several_members(sent):
    notify.handler(
        {"chat_ids": ["222", "333"], "kind": "escalation",
         "data": {"food": "caldo", "owner_name": "Lupita"}}
    )

    assert [payload["chat_id"] for _, payload in sent] == ["222", "333"]
    assert all("Lupita tiene caldo vencido" in payload["text"] for _, payload in sent)


def test_question_says_how_many_more_are_waiting():
    one = messages.build("ask", {**TUPPER, "waiting": 1})
    three = messages.build("ask", {**TUPPER, "waiting": 3})

    assert "1 alimento más por revisar" in one.text
    assert "3 alimentos más por revisar" in three.text and "uno por uno" in three.text
    assert "por revisar" not in messages.build("ask", TUPPER).text
    assert "por revisar" not in three.speech


def test_review_question_is_neutral_about_the_owner_and_cannot_be_snoozed():
    message = messages.build("ask", {**TUPPER, "review": True, "waiting": 0})

    assert "«caldo»" in message.text and "Tu caldo" not in message.text
    assert [row[0]["callback_data"] for row in message.buttons] == ["e|abc123", "t|abc123"]
    assert message.speech is None


def test_list_groups_by_urgency_and_offers_to_review_expired_items():
    message = messages.build(
        "list",
        {
            "items": [
                {"food": "pollo", "days_left": -1, "needs_review": True, "stored_by_name": ""},
                {"food": "salsa", "days_left": 1, "needs_review": False, "stored_by_name": "Ana"},
                {"food": "queso", "days_left": 9, "needs_review": False, "stored_by_name": ""},
            ],
            "review_count": 1,
        },
    )
    lines = message.text.splitlines()

    assert lines.index("⚠️ Vencidos, por revisar:") < lines.index("• pollo: ya venció")
    assert lines.index("⏰ Vencen pronto:") < lines.index("• salsa: vence en 1 día (lo guardó Ana)")
    assert lines.index("✅ En buen estado:") < lines.index("• queso: vence en 9 días")
    assert message.buttons == [[{"text": "🔎 Revisar vencidos (1)", "callback_data": "r|0"}]]


def test_no_message_talks_about_rescued_containers():
    samples = [
        ("list", {"items": [], "review_count": 0}),
        ("resolved", {"action": "eaten", "food": "caldo"}),
        ("consumed", {"found": True, "food": "caldo", "action": "eaten"}),
        ("help", {}),
    ]

    for kind, data in samples:
        text = messages.build(kind, data).text
        assert "rescatad" not in text and "tuppers registrados" not in text


def test_review_button_reply_depends_on_whether_anything_is_pending():
    assert "uno por uno" in messages.build("resolved", {"action": "review", "review_id": "T#1"}).text
    assert "No hay" in messages.build("resolved", {"action": "review", "review_id": ""}).text


def test_thrown_away_by_text_is_confirmed_without_a_health_warning():
    text = messages.build("consumed", {"found": True, "food": "caldo", "action": "tossed"}).text

    assert text == "Entendido, quité caldo del refri."


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("resolved", {"action": "eaten", "food": "caldo", "overdue": True}),
        ("consumed", {"found": True, "food": "caldo", "overdue": True}),
    ],
)
def test_eating_overdue_food_gets_a_health_message_instead_of_a_celebration(kind, data):
    text = messages.build(kind, data).text

    assert "caldo" in text and "salud" in text and "cuesta tirar comida" in text
    assert "rescatado" not in text and "🎉" not in text and "Buen provecho" not in text


def test_overdue_health_message_is_also_spoken_without_the_emoji():
    message = messages.build("resolved", {"action": "eaten", "food": "caldo", "overdue": True})

    assert message.speech == message.text.replace(" 💛", "")
    assert "salud" in message.speech


def test_eating_on_time_is_confirmed():
    text = messages.build("resolved", {"action": "eaten", "food": "caldo"}).text

    assert text == "¡Buen provecho! Quité caldo del refri."


def test_ask_for_a_deleted_tupper_does_not_create_a_phantom_item(sent, tables):
    notify.handler({"chat_id": 111, "kind": "ask", "data": TUPPER, "task_token": "TOKEN"})

    assert "Item" not in tables.get_item(Key={"chat_id": "111", "item_id": "T#abc123"})
    assert len(sent) == 1


def test_button_press_is_acknowledged_before_the_reply(sent):
    notify.handler(
        {
            "chat_id": 111,
            "kind": "resolved",
            "data": {"action": "eaten", "food": "caldo"},
            "callback_query_id": "cb-1",
        }
    )

    assert sent[0] == ("answerCallbackQuery", {"callback_query_id": "cb-1"})
    assert sent[1][0] == "sendMessage"
    assert "Quité caldo del refri" in sent[1][1]["text"]


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
        ("consumed", {"found": True, "food": "caldo"}, "Quité caldo del refri"),
        ("consumed", {"found": False}, "No encontré"),
        ("list", {"items": []}, "El refri está vacío"),
        ("list", {"items": [{"food": "caldo", "days_left": 0}]}, "caldo: ya venció"),
        ("list", {"items": [{"food": "caldo", "days_left": 2}]}, "caldo: vence en 2 días"),
        ("invite", {"code": "XYZ"}, "https://t.me/guardian_test_bot?start=care_XYZ"),
        ("link", {"linked": True, "owner_name": "Lupita"}, "Lupita"),
        ("link", {"linked": False}, "ya no es válido"),
        ("resolved", {"action": "already"}, "ya estaba resuelto"),
        ("resolved", {"action": "snooze"}, "mañana"),
        ("unanswered", {"food": "caldo"}, "vencido"),
        ("escalation", {"food": "caldo", "owner_name": "Lupita"}, "Lupita tiene caldo vencido"),
        ("escalation", {"food": "caldo", "owner_name": ""}, "Alguien de tu familia tiene caldo"),
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


def test_refused_join_explains_what_to_do_first():
    one = messages.build("link", {"linked": False, "own_items": 1}).text
    two = messages.build("link", {"linked": False, "own_items": 2}).text

    assert "1 alimento en tu refri" in one and "/refri" in one
    assert "2 alimentos en tu refri" in two and "mismo enlace" in two
    assert "ya no es válido" not in one


def test_old_snooze_button_reply_points_to_the_list():
    text = messages.build("resolved", {"action": "pending", "food": "caldo"}).text

    assert "caldo" in text and "/refri" in text and "mañana" not in text


def test_shopping_list_confirmation_names_every_food_and_the_source():
    message = messages.build(
        "stored_list",
        {
            "items": [
                {"food": "leche", "days": 7, "default_used": False},
                {"food": "chayote", "days": 2, "default_used": True},
            ],
            "skipped": ["arroz", "atún"],
        },
    )

    assert "Anoté 2 alimentos" in message.text
    assert "• leche: 7 días" in message.text
    assert "• chayote: 2 días (no lo tengo en mi tabla, usé un tiempo corto)" in message.text
    assert "FoodKeeper" in message.text
    assert "No necesitan refrigeración, consulta su fecha de caducidad: arroz, atún" in message.text
    assert message.speech and "2 alimentos" in message.speech


def test_shopping_list_with_nothing_for_the_fridge():
    text = messages.build("stored_list", {"items": [], "skipped": ["arroz"]}).text

    assert "No necesita refrigeración, consulta su fecha de caducidad: arroz" in text
    assert "Anoté" not in text


def test_shopping_list_over_the_cap_asks_for_the_rest():
    text = messages.build(
        "stored_list",
        {"items": [{"food": "leche", "days": 7}], "skipped": [], "dropped": 5},
    ).text

    assert "5 alimentos más" in text and "otro mensaje" in text


DATE_QUESTION = {"food": "atún", "tupper_id": "abc123"}


def test_pantry_food_is_stored_with_a_date_question_and_a_skip_button():
    message = messages.build(
        "stored", {"food": "atún", "awaiting_date": True, "ask_date": DATE_QUESTION}
    )

    assert "atún no necesita refrigeración" in message.text
    assert "¿Cuándo caduca tu atún?" in message.text and "foto" in message.text
    assert message.buttons == [[{"text": "⏭️ No registrar", "callback_data": "x|abc123"}]]


def test_shopping_list_asks_for_the_first_pantry_date():
    message = messages.build(
        "stored_list", {"items": [], "skipped": ["atún"], "ask_date": DATE_QUESTION}
    )

    assert "¿Cuándo caduca tu atún?" in message.text
    assert message.buttons[0][0]["callback_data"] == "x|abc123"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"outcome": "dated", "tracked": True, "food": "atún", "date": "2027-03-15", "next": {}},
            "Anoté atún con caducidad 15/03/2027",
        ),
        (
            {"outcome": "dated", "tracked": False, "food": "miel", "date": "2028-01-01", "next": {}},
            "no te mando avisos",
        ),
        ({"outcome": "past", "food": "atún"}, "ya pasó"),
        ({"outcome": "not_waiting"}, "No estoy esperando ninguna fecha"),
    ],
)
def test_date_answers(data, expected):
    assert expected in messages.build("dated", data).text


def test_date_answer_asks_the_next_food():
    message = messages.build(
        "dated",
        {"outcome": "dated", "tracked": True, "food": "arroz", "date": "2027-03-15", "next": DATE_QUESTION},
    )

    assert "¿Cuándo caduca tu atún?" in message.text
    assert message.buttons[0][0]["callback_data"] == "x|abc123"


def test_skip_reply_confirms_and_asks_the_next_food():
    message = messages.build("resolved", {"action": "skip_date", "food": "arroz", "next": DATE_QUESTION})

    assert "No registré arroz" in message.text and "¿Cuándo caduca tu atún?" in message.text


def test_list_marks_pantry_food():
    text = messages.build("list", {"items": [{"food": "atún", "days_left": 30, "pantry": True}]}).text

    assert "🥫 atún: vence en 30 días" in text
