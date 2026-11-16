import json

import pytest

from guardian import understand


class FakeBedrock:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.reply}]}}}


@pytest.fixture
def bedrock(monkeypatch):
    def install(reply):
        fake = FakeBedrock(reply if isinstance(reply, str) else json.dumps(reply))
        monkeypatch.setattr(understand, "_bedrock", fake)
        return fake

    return install


def message(**fields):
    return {"message": {"chat": {"id": 111}, "from": {"first_name": "Lupita"}, **fields}}


def test_text_is_classified_by_the_model(bedrock):
    fake = bedrock({"intent": "store", "food": "Queso  panela", "category": "fresh_cheese"})

    result = understand.handler(message(text="Guardé queso panela en el refri"))

    assert result == {
        "chat_id": 111,
        "first_name": "Lupita",
        "intent": "store",
        "food": "Queso panela",
        "category": "fresh_cheese",
        "code": "",
    }
    call = fake.calls[0]
    assert call["modelId"] == "test-model"
    # Room for a shopping list of MAX_LIST_ITEMS foods.
    assert call["inferenceConfig"]["maxTokens"] == understand.MAX_REPLY_TOKENS
    assert call["messages"][0]["content"] == [{"text": "Guardé queso panela en el refri"}]


def test_category_outside_the_closed_list_becomes_other(bedrock):
    bedrock({"intent": "store", "food": "algo", "category": "lasts_forever_365_days"})

    assert understand.handler(message(text="guardé algo"))["category"] == "other"


@pytest.mark.parametrize(
    "reply",
    [
        "no json here",
        "{broken",
        '["store"]',
        {"intent": "delete_everything", "food": "x", "category": "other"},
        {"intent": "store", "food": "", "category": "other"},
        {"intent": "store", "food": 42, "category": "other"},
    ],
)
def test_unexpected_model_output_is_unknown(bedrock, reply):
    bedrock(reply)

    result = understand.handler(message(text="hola"))

    assert result["intent"] == "unknown"
    assert result["food"] == ""


def test_model_reply_wrapped_in_prose_is_still_parsed(bedrock):
    bedrock('Sure:\n```json\n{"intent": "list", "food": "", "category": "other"}\n```')

    assert understand.handler(message(text="qué tengo"))["intent"] == "list"


def test_photo_is_downloaded_and_sent_as_an_image(bedrock, monkeypatch):
    fake = bedrock({"intent": "store", "food": "arroz", "category": "cooked_rice_pasta"})
    downloaded = []
    monkeypatch.setattr(
        understand.telegram, "download_file", lambda fid: downloaded.append(fid) or b"JPEG"
    )
    sizes = [
        {"file_id": "small", "width": 90, "height": 60},
        {"file_id": "medium", "width": 1280, "height": 853},
        {"file_id": "large", "width": 2560, "height": 1706},
    ]

    result = understand.handler(message(photo=sizes, caption="lo de ayer"))

    assert result["food"] == "arroz"
    assert downloaded == ["medium"]
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0] == {"image": {"format": "jpeg", "source": {"bytes": b"JPEG"}}}
    assert content[1] == {"text": "lo de ayer"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", {"intent": "help", "code": ""}),
        ("/ayuda", {"intent": "help", "code": ""}),
        ("/familiar", {"intent": "invite", "code": ""}),
        ("/familia", {"intent": "invite", "code": ""}),
        ("/refri", {"intent": "list", "code": ""}),
        ("/familiar@guardian_test_bot", {"intent": "invite", "code": ""}),
        ("/start care_AbC-123_x", {"intent": "link", "code": "AbC-123_x"}),
    ],
)
def test_commands_never_reach_the_model(bedrock, text, expected):
    fake = bedrock({"intent": "store", "food": "x", "category": "other"})

    result = understand.handler(message(text=text))

    assert {key: result[key] for key in expected} == expected
    assert fake.calls == []


def test_voice_and_empty_messages_are_answered_without_the_model(bedrock):
    fake = bedrock({"intent": "store", "food": "x", "category": "other"})

    assert understand.handler(message(voice={"file_id": "v"}))["intent"] == "voice_unsupported"
    assert understand.handler(message(sticker={"file_id": "s"}))["intent"] == "help"
    assert fake.calls == []


def test_missing_sender_name_defaults_to_empty(bedrock):
    bedrock({"intent": "list", "food": "", "category": "other"})

    result = understand.handler({"message": {"chat": {"id": 5}, "text": "qué tengo"}})

    assert result["first_name"] == ""
    assert result["chat_id"] == 5


def test_throwing_food_away_is_its_own_intent(bedrock):
    bedrock({"intent": "discarded", "food": "caldo", "category": "cooked_soup_stew"})

    result = understand.handler(message(text="Ya tiré el caldo"))

    assert result["intent"] == "discarded" and result["food"] == "caldo"
    assert "discarded: the user says they threw a food away" in understand.SYSTEM_PROMPT


SHOPPING_LIST = {
    "intent": "store_list",
    "items": [
        {"food": "Leche", "category": "milk_unopened"},
        {"food": "huevos", "category": "raw_eggs"},
        {"food": "arroz", "category": "pantry"},
        {"food": "", "category": "raw_eggs"},
        {"food": "leche", "category": "milk_unopened"},
        {"food": "chayote", "category": "invented_category"},
    ],
}


def test_shopping_list_becomes_one_item_per_food(bedrock):
    bedrock(SHOPPING_LIST)

    result = understand.handler(message(text="Lista del súper: leche, huevos, arroz, chayote"))

    assert result["intent"] == "store_list"
    assert result["items"] == [
        {"food": "Leche", "category": "milk_unopened"},
        {"food": "huevos", "category": "raw_eggs"},
        {"food": "chayote", "category": "other"},
    ]
    # Pantry food does not go in the fridge: reported, never tracked.
    assert result["skipped"] == ["arroz"]


def test_shopping_list_is_capped(bedrock):
    many = [{"food": f"fruta {n}", "category": "whole_fruit"} for n in range(40)]
    bedrock({"intent": "store_list", "items": many})

    result = understand.handler(message(text="mi lista"))

    assert len(result["items"]) == understand.MAX_LIST_ITEMS


@pytest.mark.parametrize(
    "items",
    [[], "leche", [{"food": "", "category": "x"}], [{"nope": 1}], None],
)
def test_shopping_list_without_any_food_is_unknown(bedrock, items):
    bedrock({"intent": "store_list", "items": items})

    assert understand.handler(message(text="lista"))["intent"] == "unknown"


def test_shopping_list_with_only_pantry_food_is_still_answered(bedrock):
    bedrock({"intent": "store_list", "items": [{"food": "atún en lata", "category": "pantry"}]})

    result = understand.handler(message(text="compré atún"))

    assert (result["intent"], result["items"], result["skipped"]) == (
        "store_list",
        [],
        ["atún en lata"],
    )


def test_prompt_explains_shopping_lists_and_grocery_categories():
    prompt = understand.SYSTEM_PROMPT
    assert "store_list" in prompt and "pantry" in prompt
    assert {"raw_eggs", "milk_unopened", "whole_fruit", "root_vegetables"} <= understand.CATEGORIES
    # "pantry" is a list-only marker, never a shelf-life category.
    assert "pantry" not in understand.CATEGORIES


def test_shopping_list_over_the_cap_says_how_many_were_left_out(bedrock):
    many = [{"food": f"fruta {n}", "category": "whole_fruit"} for n in range(25)]
    bedrock({"intent": "store_list", "items": many})

    result = understand.handler(message(text="mi lista"))

    assert result["dropped"] == 5


def test_prompt_leaves_non_food_products_out_of_the_list():
    assert "Leave out non-food products" in understand.SYSTEM_PROMPT
